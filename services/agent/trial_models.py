"""Paired provider trials on a frozen synthetic fixture.

Default is a dry run. Live trials use exported provider credentials and synthetic
data only. The runner uses strict tools for both providers and disables fallback.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time

import brain
from providers import GROQ_MODELS, GeminiProvider, GroqProvider
from tools import TripData

FIXTURE = Path(__file__).with_name("trial_cases.json")


class TrialData(TripData):
    """Production read/validation methods over an in-memory synthetic universe."""

    def __init__(self, fixture, case):
        self.itinerary_id = None
        self.days = [] if case.get("no_plan") else deepcopy(fixture["days"])
        self.places = {p["poi_id"]: deepcopy(p) for p in fixture["places"]}
        self.group = list(self.places)
        self.recommendation_reasons = {}
        # None disables the production method's database catalogue top-up.
        self.version = None
        self.hours = fixture["hours"] | case.get("hours", {})

    def check_hours(self, poi_id, day, start, duration_min=90):
        available = self.hours.get(poi_id)
        return {"poi_id": poi_id, "available": available,
                "reason": "Synthetic fixture: closed" if available is False else "Synthetic fixture"}


def trial_request(case, language):
    return {"trip_id": "synthetic", "itinerary_id": None, "recent_messages": [],
            "message": {"user_id": "synthetic-member", "text": "@TravelSync " + case[language]}}


def run_trials(fixture, *, live=False, repeats=1, models=GROQ_MODELS,
               pause_seconds=10, provider_factory=GroqProvider, providers=None,
               first_round=1, languages=("en", "ko"), retries=0):
    providers = providers or dict.fromkeys(models, "groq")
    records = []
    for repeat in range(first_round - 1, first_round - 1 + repeats):
        for case_index, case in enumerate(fixture["cases"]):
            for language_index, language in enumerate(languages):
                # Alternate first model, counterbalancing model order across cases/languages.
                order = models if (repeat + case_index + language_index) % 2 == 0 else models[::-1]
                for model in order:
                    req = trial_request(case, language)
                    record = {"case": case["id"], "language": language, "repeat": repeat + 1,
                              "provider": providers[model], "model": model, "request": req,
                              "expects_edit": case["expect_proposal"],
                              "status": "not_run", "response": None, "metrics": {}, "checks": {}}
                    if live:
                        if records and pause_seconds:
                            time.sleep(pause_seconds)
                        turn_started = time.monotonic()
                        attempts = []
                        for attempt in range(retries + 1):
                            if attempt:
                                time.sleep(min(60, max(15, pause_seconds) * 2 ** (attempt - 1)))
                            result = run_turn(fixture, case, language, req, model, provider_factory)
                            attempts.append(result)
                            if result["status"] == "answered":
                                break
                        record.update(attempts[-1])
                        if retries:
                            record["attempts"] = attempts
                            record["elapsed_seconds"] = round(time.monotonic() - turn_started, 3)
                    records.append(record)
    return records


def run_turn(fixture, case, language, req, model, provider_factory):
    record = {"metrics": {}, "checks": {}, "response": None}
    started = time.monotonic()
    record["started_at"] = datetime.now(timezone.utc).isoformat()
    try:
        out = brain.model_turn(req, TrialData(fixture, case), provider_factory(model),
                               metrics=record["metrics"])
        record.update(status="answered", response=out)
        record["checks"] = {
            "proposal_presence_matches_case": bool(out["proposal"]) == case["expect_proposal"],
            "clarification_matches_case": (out["needs_clarification"] == case["expect_clarification"]
                                           if "expect_clarification" in case else None),
            "language_heuristic_matches": bool(re.search(r"[가-힣]", out["reply"])) == (language == "ko"),
            "submitted_proposal_accepted": (not record["metrics"]["proposal_rejected"]
                                            if record["metrics"]["proposal_submitted"] else None),
        }
    except Exception as error:
        record.update(status="failed", error=brain.provider_failure_summary(error))
        record["http_status"] = getattr(error, "code", None)
        record["timed_out"] = isinstance(error, TimeoutError) or record["http_status"] == 504
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return record


def build_report(fixture_bytes, *, live=False, repeats=1, models=GROQ_MODELS, pause_seconds=10,
                 provider_factory=GroqProvider, providers=None, first_round=1,
                 case_id=None, languages=("en", "ko"), account_tiers=None, retries=0):
    fixture = json.loads(fixture_bytes)
    providers = providers or dict.fromkeys(models, "groq")
    if case_id:
        fixture["cases"] = [case for case in fixture["cases"] if case["id"] == case_id]
    records = run_trials(fixture, live=live, repeats=repeats, models=models,
                         pause_seconds=pause_seconds, provider_factory=provider_factory,
                         providers=providers, first_round=first_round, languages=languages, retries=retries)
    summaries = {}
    for model in models:
        rows = [r for r in records if r["model"] == model]
        answered = [r for r in rows if r["status"] == "answered"]
        summaries[model] = {"planned": len(rows), "answered": len(answered),
                            "failed": sum(r["status"] == "failed" for r in rows),
                            "not_run": sum(r["status"] == "not_run" for r in rows),
                            "checks_all_true": sum(all(v is not False for v in r["checks"].values()) for r in answered)}
    return {"created_at": datetime.now(timezone.utc).isoformat(), "mode": "live" if live else "dry_run",
            "fixture_version": fixture["version"], "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
            "source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                              for name in ("brain.py", "providers.py", "tools.py", "trial_models.py")},
            "system_sha256": hashlib.sha256(brain.SYSTEM.encode("utf-8")).hexdigest(),
            "tools_sha256": hashlib.sha256(json.dumps(brain.strict_tools(), sort_keys=True).encode("utf-8")).hexdigest(),
            "settings": {"models": list(models), "repeats": repeats, "temperature": 0.2,
                         "providers": providers, "account_tiers": account_tiers or {},
                         "first_round": first_round, "case_filter": case_id, "languages": list(languages),
                         "reasoning_effort": {model: ("provider_default" if providers[model] == "gemini" else "low")
                                              for model in models},
                         "max_completion_tokens": {model: (None if providers[model] == "gemini" else 2048)
                                                   for model in models},
                         "retries": retries,
                         "max_steps": brain.MAX_STEPS, "deadline_seconds": brain.DEADLINE_SECONDS,
                         "pause_seconds": pause_seconds, "rules_fallback": False},
            "limitations": ["Synthetic fixtures; not real place facts, relevance or satisfaction labels.",
                            "Language check only detects Hangul; human bilingual review is required.",
                            "Proposal checks do not establish end-to-end itinerary feasibility.",
                            "API failures are retained. No cross-model retry or rules fallback.",
                            "Rate limits can still be reached within a multi-step turn."],
            "summary": summaries, "records": records}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send synthetic requests to the selected providers")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--models", nargs=2, default=list(GROQ_MODELS))
    parser.add_argument("--compare-gemini", action="store_true", help="Compare Gemini primary with Qwen on Groq")
    parser.add_argument("--gemini-model", default=brain.GEMINI_MODEL)
    parser.add_argument("--gemini-tier", default="unknown")
    parser.add_argument("--groq-tier", default="unknown")
    parser.add_argument("--first-round", type=int, default=1)
    parser.add_argument("--retries", type=int, choices=range(6), default=0)
    parser.add_argument("--case", choices=[case["id"] for case in json.loads(FIXTURE.read_bytes())["cases"]])
    parser.add_argument("--language", choices=("en", "ko"))
    parser.add_argument("--pause-seconds", type=float, default=10)
    parser.add_argument("--output", type=Path, required=True, help="New report path; existing files are preserved")
    args = parser.parse_args(argv)
    models = (args.gemini_model, GROQ_MODELS[1]) if args.compare_gemini else tuple(args.models)
    if args.repeats < 1 or args.first_round < 1 or not 0 <= args.pause_seconds <= 60 or len(set(models)) != 2:
        parser.error("Require repeats >= 1, two distinct models, and pause between 0 and 60 seconds")
    if args.live and not os.getenv("GROQ_API_KEY"):
        parser.error("Export GROQ_API_KEY before --live; .env files are not loaded automatically")
    if args.live and args.compare_gemini and not os.getenv("GEMINI_API_KEY"):
        parser.error("Export GEMINI_API_KEY before --live; .env files are not loaded automatically")
    if args.output.exists():
        parser.error("Output already exists; choose a new report path")
    providers = dict.fromkeys(models, "groq")
    if args.compare_gemini:
        providers[args.gemini_model] = "gemini"

    def provider_factory(model):
        return GeminiProvider(model, think=False) if providers[model] == "gemini" else GroqProvider(model)

    report = build_report(FIXTURE.read_bytes(), live=args.live, repeats=args.repeats,
                          models=models, pause_seconds=args.pause_seconds, providers=providers,
                          provider_factory=provider_factory, first_round=args.first_round,
                          case_id=args.case, languages=(args.language,) if args.language else ("en", "ko"),
                          account_tiers={"groq": args.groq_tier, "gemini": args.gemini_tier}, retries=args.retries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"mode": report["mode"], "summary": report["summary"]}, ensure_ascii=False))
    return 1 if any(r["status"] == "failed" for r in report["records"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
