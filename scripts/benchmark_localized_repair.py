"""Offline, identical-fixture engine comparison; not a live latency benchmark.

Run: python scripts/benchmark_localized_repair.py --output docs/localized_repair_benchmark.json
The legacy baseline is the full multi-day solve with retained visits locked by date.
Service retrieval/enrichment, voting time, and network latency are outside this measurement.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import subprocess
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_localized_repair import scenario
from ourney_travelsync.optimizer.api import optimize
from ourney_travelsync.optimizer.contracts import Lock
from ourney_travelsync.optimizer.repair import repair
from ourney_travelsync.optimizer.validation import validate_final


def summary(rows):
    durations = sorted(r["seconds"] for r in rows)
    # Nearest-rank p95, explicit for this small sample.
    import math
    return dict(samples=len(rows), median_seconds=statistics.median(durations),
                p95_seconds=durations[math.ceil(.95*len(durations))-1],
                feasible=sum(r["feasible"] for r in rows),
                full_optimizer_invocations=sum(r["full_optimizer_invocations"] for r in rows),
                hard_constraint_violations=sum(r["hard_constraint_violations"] for r in rows),
                unrelated_items_changed=[r["unrelated_items_changed"] for r in rows],
                solver_timeouts=sum(r["solver_status"] == "UNKNOWN" for r in rows))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=20)
    args = parser.parse_args()
    req, days = scenario(2)
    baseline = req.model_copy(update={"candidates": tuple(p for p in req.candidates if p.poi_id != "A"),
        "locked_items": tuple(Lock(poi_id=pid, date=days[k].date)
                              for k, ids in enumerate((("C", "B"), ("D", "E"))) for pid in ids),
        "gap_policy": req.gap_policy.model_copy(update={"enabled": False})})
    old, new = [], []
    for _ in range(args.repeats):
        for mode in ("legacy_full_solve", "localized"):
            started = perf_counter()
            if mode == "legacy_full_solve":
                result = optimize(baseline)
                output, status, strategy = result.days, result.solver_status, "full"
                feasible = bool(output) and validate_final(baseline, output).passed
                solver_seconds = sum(p.wall_seconds for p in result.phases)
            else:
                result = repair(req, days, days[0].date, 0, "C")
                output, status, strategy = result.days, "NOT_RUN", result.strategy
                feasible = bool(output) and validate_final(req, output).passed
                solver_seconds = 0
            duration = perf_counter()-started
            original = {v.poi_id: (d.date.isoformat(), v.start_min) for d in days for v in d.items if v.poi_id != "A"}
            after = {v.poi_id: (d.date.isoformat(), v.start_min) for d in output for v in d.items}
            checked = validate_final(baseline if mode == "legacy_full_solve" else req, output) if output else None
            row = dict(seconds=duration, feasible=feasible, strategy=strategy, solver_status=status,
                       solver_seconds=solver_seconds, full_optimizer_invocations=int(mode == "legacy_full_solve"),
                       external_routing_api_calls=0, hard_constraint_violations=len(checked.errors) if checked else 0,
                       unrelated_items_changed=sum(after.get(pid) != timing for pid, timing in original.items()) if output else None,
                       per_member_utility=checked.metrics.get("per_member_utility") if checked and checked.passed else None)
            (old if mode == "legacy_full_solve" else new).append(row)
    payload = dict(kind="synthetic engine microbenchmark", python=platform.python_version(),
                   fixture_sha256=hashlib.sha256(req.model_dump_json().encode()).hexdigest(),
                   source_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                   source_hashes={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
                       "src/ourney_travelsync/optimizer/repair.py", "src/ourney_travelsync/optimizer/solver.py",
                       "src/ourney_travelsync/optimizer/contracts.py", "src/ourney_travelsync/optimizer/validation.py",
                       "tests/test_localized_repair.py", "tests/optimizer_fixtures.py")},
                   repeats=args.repeats, seed=req.seed,
                   search_seconds=req.search_seconds, live_services=False,
                   limitations=["No LLM, network, database or voting latency measured", "Not the historical November 26 production itinerary",
                                "Legacy baseline reproduces the full-solve strategy, not the whole old HTTP pipeline",
                                "Application success and LLM timeout rates are not measured by this script"],
                   legacy=dict(summary=summary(old), rows=old), localized=dict(summary=summary(new), rows=new))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"legacy": summary(old), "localized": summary(new)}, indent=2))


if __name__ == "__main__":
    main()
