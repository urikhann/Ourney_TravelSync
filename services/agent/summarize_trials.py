"""Summarize recorded paired trials without model calls or production access."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from statistics import median


def summarize(report, reviews=None, billed_costs=None):
    if report.get("mode") != "live" or not report.get("records"):
        raise ValueError("A nonempty live trial report is required; dry runs are not evidence")
    models = report["settings"]["models"]
    result = {}
    reviews = reviews or {}
    billed_costs = billed_costs or {}
    for model in models:
        rows = [r for r in report["records"] if r["model"] == model]
        attempts = [attempt for row in rows for attempt in row.get("attempts", [row])
                    if attempt["status"] != "not_run"]
        answered = [r for r in rows if r["status"] == "answered"]
        passing = [r for r in answered if r.get("checks") and all(v is not False for v in r["checks"].values())]
        validated = [r for r in answered if (r.get("response") or {}).get("proposal")]
        edit_rows = [r for r in rows if r.get("expects_edit") is True]
        reviewed = [reviews[key] for r in answered
                    if (key := (model, r.get("repeat"), r.get("case"), r["language"])) in reviews]
        complete_review = len(reviewed) == len(answered) and all(r["status"] != "not_run" for r in rows)
        billed = billed_costs.get(model)
        result[model] = dict(planned=len(rows), answered=len(answered),
            failed=sum(r["status"]=="failed" for r in rows),
            automatic_checks_passed=len(passing),
            pass_rate_all_planned=len(passing)/len(rows) if rows else None,
            answer_rate_all_planned=len(answered)/len(rows) if rows else None,
            validated_proposals=len(validated),
            edit_turns_planned=len(edit_rows) if all("expects_edit" in r for r in rows) else None,
            validated_proposal_rate_on_edit_turns=(
                sum(r.get("expects_edit") is True for r in validated) / len(edit_rows) if edit_rows else None),
            bilingual_reviews_completed=len(reviewed),
            correct_outcome_rate_all_planned=sum(reviewed) / len(rows) if complete_review and rows else None,
            successful_turn_median_seconds=median(r["elapsed_seconds"] for r in answered) if answered else None,
            attempted_turn_median_seconds=median(latencies) if (
                latencies := [r["elapsed_seconds"] for r in rows if "elapsed_seconds" in r]) else None,
            attempts=len(attempts),
            first_attempt_answered=sum(row.get("attempts", [row])[0]["status"] == "answered" for row in rows),
            http_status_counts=dict(Counter(r["http_status"] for r in attempts if r.get("http_status") is not None)),
            timed_out=sum(bool(r.get("timed_out")) for r in attempts),
            returned_tokens=sum(r.get("metrics",{}).get("usage",{}).get("total_tokens",0) for r in attempts),
            returned_usage=dict(sum((Counter(r.get("metrics", {}).get("usage", {})) for r in attempts), Counter())),
            billed_cost_usd=billed,
            billed_cost_per_validated_edit_usd=billed / len(validated) if billed is not None and validated else None,
            model_requests=sum(r.get("metrics",{}).get("model_requests",0) for r in attempts),
            errors=dict(Counter(r.get("error","unspecified") for r in attempts if r["status"]=="failed")),
            by_language={language:dict(answered=sum(r["status"]=="answered" for r in rows if r["language"]==language),
                planned=sum(r["language"]==language for r in rows)) for language in ("en","ko")})
    return dict(created_at=report.get("created_at"), fixture_sha256=report["fixture_sha256"],
        settings=report["settings"], summary=result,
        limitations=["Synthetic benchmark; automatic checks require human bilingual review.",
                     "Failures remain in the denominator; rate limits confound quality comparison.",
                     "Successful-turn latency excludes failures; token totals exclude unreported usage.",
                     "Billed costs require provider records attributable to this run.",
                     "No evidence of real venue feasibility or user satisfaction."])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--reviews", type=Path, help="JSON list with model, repeat, case, language and correct")
    parser.add_argument("--billed-costs", type=Path, help="JSON object mapping model to run-attributable USD cost")
    args=parser.parse_args()
    raw=args.report.read_bytes()
    review_rows = json.loads(args.reviews.read_bytes()) if args.reviews else []
    reviews = {}
    for row in review_rows:
        key = (row["model"], row["repeat"], row["case"], row["language"])
        if key in reviews or type(row["correct"]) is not bool:
            parser.error("Reviews require unique turn keys and Boolean correctness")
        reviews[key] = row["correct"]
    billed_costs = json.loads(args.billed_costs.read_bytes()) if args.billed_costs else {}
    result=summarize(json.loads(raw), reviews, billed_costs)
    result["source_report_sha256"]=hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result["summary"],ensure_ascii=False,indent=2))


if __name__=="__main__": main()
