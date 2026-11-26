"""Opt-in paired HTTP benchmark over disposable SQLite and synthetic services."""
import json
import hashlib
import math
import os
from pathlib import Path
import statistics
import subprocess
import types
from time import perf_counter

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import services
from app.db import SessionLocal
from app.models import OptimizationRun
from sqlalchemy import select
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.validation import validate_final
from app.replacements import plans
from .test_proposal_approval import planned_trip, swap_for, current_plan, replacement_fixture_clock
from .test_optimizer_integration import c


@pytest.mark.skipif(os.getenv("TRAVELSYNC_RUN_REPLACEMENT_BENCHMARK") != "1", reason="opt-in paired benchmark")
def test_paired_http_benchmark(planning_adapter, replacement_fixture_clock, monkeypatch):
    """Legacy chat routes from HEAD, current implementation on the identical fixtures."""
    root = Path(__file__).resolve().parents[2]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    def legacy_module(name, path):
        source = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=root).decode("utf-8")
        module = types.ModuleType(name)
        exec(compile(source, f"{revision}:{path}", "exec"), module.__dict__)
        return module
    old_proposals = legacy_module("app._benchmark_proposals", "api/app/proposals.py")
    old_chat = legacy_module("app.routes._benchmark_chat", "api/app/routes/chat.py")
    old_chat.proposals = old_proposals
    legacy_app = FastAPI()
    legacy_app.include_router(old_chat.router)
    legacy_client = TestClient(legacy_app)
    build, data = services.build_plans, services.planning_data
    counts = {"solver": 0, "data": 0, "legs": 0}
    async def solve(payload):
        counts["solver"] += 1
        return await build(payload)
    async def snapshot(payload, **kw):
        counts["data"] += 1
        counts["legs"] += len(payload.get("route_legs", []))
        return await data(payload, **kw)
    monkeypatch.setattr(services, "build_plans", solve)
    monkeypatch.setattr(services, "planning_data", snapshot)
    rows = []
    for repeat in range(5):
        for mode, client in (("legacy", legacy_client), ("localized", c)):
            (h,), tid, before = planned_trip(1)
            _, _, proposal = swap_for(tid, before)
            async def agent(payload):
                return {"reply": "Synthetic replacement request", "proposal": proposal, "tool_calls": []}
            monkeypatch.setattr(services, "agent_turn", agent)
            counts.update(solver=0, data=0, legs=0)
            started = perf_counter()
            response = client.post(f"/trips/{tid}/chat", headers=h, json={"text": "@TravelSync it's raining"})
            proposal_seconds = perf_counter()-started
            assert response.status_code == 201, response.text
            msg = response.json()["messages"][1]
            approval_started = perf_counter()
            approved = client.post(f"/trips/{tid}/chat/{msg['id']}/vote", headers=h, json={"approve": True})
            approval_seconds = perf_counter()-approval_started
            applied = approved.status_code == 200 and approved.json()["messages"][0]["agent"].get("proposal_status") == "APPLIED"
            after = current_plan(h, tid)
            removed = set(proposal["remove_item_ids"])
            retained = {i["poi_id"]: (d["date"], i["start"], i["end"]) for d in before["days"] for i in d["items"]
                        if f"{d['date']}@{i['start']}" not in removed}
            actual = {i["poi_id"]: (d["date"], i["start"], i["end"]) for d in after["days"] for i in d["items"]}
            with SessionLocal() as db:
                audit = db.scalar(select(OptimizationRun).where(OptimizationRun.trip_id == tid,
                    OptimizationRun.status == "PASSED", OptimizationRun.itinerary_id == after["id"]))
                validation = validate_final(PlanRequest.model_validate(audit.optimizer_request), plans(after["days"]))
                solver_seconds = sum(p.get("wall_seconds", 0) for p in audit.response.get("phases", []))
            rows.append(dict(mode=mode, repeat=repeat, proposal_seconds=proposal_seconds,
                approval_seconds=approval_seconds, total_seconds=proposal_seconds+approval_seconds,
                votable=bool(msg["agent"].get("proposal")),
                validated_before_vote=msg["agent"].get("validation_status") == "PASSED", applied=applied,
                full_optimizer_invocations=counts["solver"], planning_adapter_calls=counts["data"],
                selected_route_legs_requested=counts["legs"], live_external_routing_calls=0,
                unrelated_items_modified=sum(actual.get(pid) != values for pid, values in retained.items()),
                hard_constraint_violations=len(validation.errors), solver_seconds=solver_seconds,
                per_member_utility=validation.metrics.get("per_member_utility"),
                minimum_member_utility=validation.metrics.get("minimum_member_utility"),
                utility_disparity=validation.metrics.get("utility_disparity"),
                original_preserved_on_failure=(not applied and before == after) if not applied else None,
                approval_result=approved.json().get("messages", [{}])[0].get("agent", {}).get("result")))
    summary = {}
    for mode in ("legacy", "localized"):
        selected = [r for r in rows if r["mode"] == mode]
        elapsed = sorted(r["total_seconds"] for r in selected)
        summary[mode] = dict(samples=len(selected), median_seconds=statistics.median(elapsed),
            p95_seconds=elapsed[math.ceil(.95*len(elapsed))-1], applied=sum(r["applied"] for r in selected),
            validated_before_vote=sum(r["validated_before_vote"] for r in selected),
            full_optimizer_invocations=sum(r["full_optimizer_invocations"] for r in selected),
            unrelated_items_modified=sum(r["unrelated_items_modified"] for r in selected))
    output = root / "docs/localized_repair_http_benchmark.json"
    output.write_text(json.dumps(dict(kind="paired synthetic HTTP benchmark", baseline_revision=revision,
        repeats=5, seed=0, solver_cap_seconds=2, live_services=False,
        limitations=["One member approves immediately; human voting delay excluded", "LLM responses and operational/routing services are synthetic",
                     "Legacy routes/proposals loaded from recorded HEAD; shared services use this checkout",
                     "Small sample; p95 is nearest-rank maximum", "No live external routing API or historical production itinerary"],
        source_hashes={name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in (
            "api/app/replacements.py", "api/app/proposals.py", "api/app/routes/chat.py", "api/app/config.py",
            "api/app/routes/itineraries.py", "api/app/services.py", "api/tests/conftest.py",
            "src/ourney_travelsync/optimizer/repair.py", "src/ourney_travelsync/optimizer/contracts.py",
            "src/ourney_travelsync/optimizer/solver.py", "src/ourney_travelsync/optimizer/validation.py",
            "src/ourney_travelsync/replacement_intent.py", "api/tests/test_replacement_benchmark.py")},
        summary=summary, rows=rows), indent=2)+"\n", encoding="utf-8")
    assert summary["localized"]["applied"] == 5, summary
