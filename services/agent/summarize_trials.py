"""Summarize recorded paired trials without model calls or production access."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from statistics import median


def summarize(report):
    if report.get("mode") != "live" or not report.get("records"):
        raise ValueError("A nonempty live trial report is required; dry runs are not evidence")
    models = report["settings"]["models"]
    result = {}
    for model in models:
        rows = [r for r in report["records"] if r["model"] == model]
        answered = [r for r in rows if r["status"] == "answered"]
        passing = [r for r in answered if r.get("checks") and all(v is not False for v in r["checks"].values())]
        result[model] = dict(planned=len(rows), answered=len(answered),
            failed=sum(r["status"]=="failed" for r in rows),
            automatic_checks_passed=len(passing),
            pass_rate_all_planned=len(passing)/len(rows) if rows else None,
            answer_rate_all_planned=len(answered)/len(rows) if rows else None,
            validated_proposals=sum(bool((r.get("response") or {}).get("proposal")) for r in answered),
            successful_turn_median_seconds=median(r["elapsed_seconds"] for r in answered) if answered else None,
            returned_tokens=sum(r.get("metrics",{}).get("usage",{}).get("total_tokens",0) for r in rows),
            model_requests=sum(r.get("metrics",{}).get("model_requests",0) for r in rows),
            errors=dict(Counter(r.get("error","unspecified") for r in rows if r["status"]=="failed")),
            by_language={language:dict(answered=sum(r["status"]=="answered" for r in rows if r["language"]==language),
                planned=sum(r["language"]==language for r in rows)) for language in ("en","ko")})
    return dict(created_at=report.get("created_at"), fixture_sha256=report["fixture_sha256"],
        settings=report["settings"], summary=result,
        limitations=["Single synthetic pilot; automatic checks require human bilingual review.",
                     "Failures remain in the denominator; rate limits confound quality comparison.",
                     "Latency excludes failed turns; token totals exclude unreported failed-response usage.",
                     "No evidence of real venue feasibility or user satisfaction."])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    raw=args.report.read_bytes()
    result=summarize(json.loads(raw))
    result["source_report_sha256"]=hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result["summary"],ensure_ascii=False,indent=2))


if __name__=="__main__": main()
