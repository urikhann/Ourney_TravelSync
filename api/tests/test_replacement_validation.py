"""Pre-vote regression/integration tests. External services use labeled fixtures."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import replacements, services
from app.db import SessionLocal
from app.models import ChatMessage, Itinerary, OptimizationRun
from ourney_travelsync.optimizer.repair import RepairResult
from ourney_travelsync.optimizer.validation import ValidationResult
from .test_proposal_approval import planned_trip, swap_for, ask, vote, current_plan
from .test_optimizer_integration import c


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(replacements, "current_time", lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
    from app.routes import recommendations
    original = recommendations.build_request
    def seeded(*args):
        request, members, version = original(*args)
        request["options"]["tie_seed"] = 0
        return request, members, version
    monkeypatch.setattr(recommendations, "build_request", seeded)


def test_tc008_next_candidate_and_tc025_no_optimizer(planning_adapter, monkeypatch):
    (h,), tid, plan = planned_trip(1)
    _, first, proposal = swap_for(tid, plan)
    def unknown_first(req, response):
        for p in response["pois"]:
            if p["poi_id"] == first:
                for op in p["operations"].values():
                    op["openings"] = {}
        return response
    planning_adapter.transform = unknown_first
    async def forbidden(*args, **kwargs):
        raise AssertionError("fixed-slot replacement must not optimize")
    monkeypatch.setattr(services, "build_plans", forbidden)
    msg = ask(h, tid, proposal)
    assert msg["agent"].get("validation_status") == "PASSED", msg
    assert msg["agent"]["proposal"]["add"][0]["poi_id"] != first
    assert msg["agent"]["repair_strategy"] in ("fixed", "local")
    assert current_plan(h, tid) == plan


@pytest.mark.parametrize("kind,reason", [("hours", "opening_hours_unknown"), ("route", "routing_data_unavailable")])
def test_tc006_027_no_unknown_evidence_reaches_voting(planning_adapter, kind, reason):
    (h,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)
    def unknown(req, response):
        if kind == "route":
            response["edges"] = []
        else:
            for p in response["pois"]:
                for op in p["operations"].values():
                    op["openings"] = {}
        return response
    planning_adapter.transform = unknown
    msg = ask(h, tid, proposal)
    assert msg["agent"]["proposal"] is None and msg["agent"]["reason"] == reason
    assert current_plan(h, tid) == plan


def test_tc010_day_only_solver_and_tc012_timeout(planning_adapter, monkeypatch):
    (h,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)
    monkeypatch.setattr(replacements, "repair", lambda *a, **kw: RepairResult(reason="local_repair_failed"))
    calls = []
    async def unknown(payload):
        calls.append(payload)
        return dict(run_id=payload["run_id"], input_snapshot=payload, solver_status="UNKNOWN", error_code="SEARCH_TIMEOUT")
    monkeypatch.setattr(services, "build_plans", unknown)
    msg = ask(h, tid, proposal)
    assert calls and all(len(p["days"]) == 1 for p in calls)
    assert all({l["poi_id"] for l in p["locked_items"]} == {q["poi_id"] for q in p["candidates"]} for p in calls)
    assert msg["agent"]["reason"] == "replacement_search_timeout"
    assert msg["agent"]["proposal"] is None and current_plan(h, tid) == plan


def test_tc013_replacement_bypasses_unavailable_llm(planning_adapter, monkeypatch):
    (h,), tid, plan = planned_trip(1)
    stop, _, _ = swap_for(tid, plan)
    async def timeout(*args):
        raise AssertionError("clear replacement requests do not call the LLM")
    monkeypatch.setattr(services, "agent_turn", timeout)
    msg = c.post(f"/trips/{tid}/chat", headers=h,
                 json={"text": f"@TravelSync {stop['place']['name']} 대신 다른 곳 추천해줘."}).json()["messages"][1]
    assert msg["agent"].get("validation_status") == "PASSED", msg
    assert any("가" <= ch <= "힣" for ch in msg["text"])


def test_tc018_constraints_change_during_vote(planning_adapter):
    (h,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)
    msg = ask(h, tid, proposal)
    assert c.put(f"/trips/{tid}/availability", headers=h, json={"dates": []}).status_code == 200
    result = vote(h, tid, msg["id"]).json()["messages"][0]["agent"]["result"]
    after = current_plan(h, tid)
    assert result["status"] == "STALE" and after["id"] == plan["id"] and after["days"] == plan["days"]


def test_tc020_e3_failure_on_approval_keeps_original(planning_adapter, monkeypatch):
    (h,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)
    msg = ask(h, tid, proposal)
    monkeypatch.setattr(replacements, "validate_final", lambda *a: ValidationResult(("fixture forced E3 failure",)))
    result = vote(h, tid, msg["id"]).json()["messages"][0]["agent"]["result"]
    assert result["status"] == "FAILED" and current_plan(h, tid) == plan


def test_tc024_database_failure_rolls_back_whole_application(planning_adapter, monkeypatch):
    (h,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)
    msg = ask(h, tid, proposal)
    from sqlalchemy.orm import Session
    commit = Session.commit
    def fail(self):
        if any(isinstance(obj, ChatMessage) and obj.id == msg["id"] and
               (obj.agent or {}).get("proposal_status") == "APPLIED" for obj in self.identity_map.values()):
            raise OperationalError("synthetic commit failure", {}, Exception("fixture"))
        return commit(self)
    monkeypatch.setattr(Session, "commit", fail)
    result = vote(h, tid, msg["id"])
    assert result.status_code == 503, result.text
    assert current_plan(h, tid) == plan
    with SessionLocal() as db:
        assert db.get(ChatMessage, msg["id"]).agent["proposal_status"] == "PENDING"
        assert db.get(OptimizationRun, msg["agent"]["draft_id"]).status == "VALIDATED"
        assert len(db.scalars(select(Itinerary).where(Itinerary.trip_id == tid)).all()) == 1


def test_tc028_changed_route_requires_new_vote(planning_adapter):
    (h,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)
    msg = ask(h, tid, proposal)
    def changed(req, response):
        for e in response["edges"]:
            e["duration_minutes"] += 1
        return response
    planning_adapter.transform = changed
    result = vote(h, tid, msg["id"]).json()["messages"][0]["agent"]["result"]
    assert result["status"] == "STALE" and result["reason"] == "proposal_evidence_changed"
    assert current_plan(h, tid) == plan


def test_expired_and_legacy_proposal_not_applied(planning_adapter, monkeypatch):
    (h,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)
    msg = ask(h, tid, proposal)
    monkeypatch.setattr(replacements, "current_time", lambda: datetime(2026, 11, 1, 1, tzinfo=timezone.utc))
    result = vote(h, tid, msg["id"]).json()["messages"][0]["agent"]["result"]
    assert result["status"] == "STALE"
    assert current_plan(h, tid) == plan


def test_tc021_ambiguous_request_clarifies(planning_adapter):
    (h,), tid, plan = planned_trip(1)
    # Generic relative reference has no uniquely identified anchor in this request.
    msg = c.post(f"/trips/{tid}/chat", headers=h,
                 json={"text": "@TravelSync 이전 일정 대신 다른 장소 넣어줘"}).json()["messages"][1]
    assert msg["agent"]["state"] == "NEEDS_CLARIFICATION"
    assert msg["agent"]["proposal"] is None and current_plan(h, tid) == plan


def test_concurrent_approvals_apply_once_and_keep_original(planning_adapter):
    from concurrent.futures import ThreadPoolExecutor
    from copy import deepcopy
    (h,), tid, original = planned_trip(1)
    with SessionLocal() as db:
        saved_original = deepcopy(db.get(Itinerary, original["id"]).days)
    _, _, proposal = swap_for(tid, original)
    msg = ask(h, tid, proposal)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: vote(h, tid, msg["id"]), range(2)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert all(r.json()["messages"][0]["agent"]["proposal_status"] == "APPLIED" for r in responses)
    with SessionLocal() as db:
        versions = db.scalars(select(Itinerary).where(Itinerary.trip_id == tid)).all()
        assert len(versions) == 2
        assert db.get(Itinerary, original["id"]).days == saved_original
    view = c.get(f"/trips/{tid}/itineraries", headers=h).json()
    assert view["chosen_id"] == view["plans"][0]["id"]


def test_started_activity_cannot_be_replaced(planning_adapter, monkeypatch):
    from copy import deepcopy
    (h,), tid, plan = planned_trip(1)
    with SessionLocal() as db:
        original_days = deepcopy(db.get(Itinerary, plan["id"]).days)
    _, _, proposal = swap_for(tid, plan)
    monkeypatch.setattr(replacements, "current_time", lambda: datetime(2026, 11, 7, tzinfo=timezone.utc))
    msg = ask(h, tid, proposal)
    assert msg["agent"]["proposal"] is None and msg["agent"]["reason"] == "locked_activity_conflict"
    assert current_plan(h, tid)["id"] == plan["id"]
    with SessionLocal() as db:
        assert db.get(Itinerary, plan["id"]).days == original_days


def test_group_can_select_preserved_original_before_a_new_request(planning_adapter):
    from app import proposals
    (h,), tid, original = planned_trip(1)
    _, _, proposal = swap_for(tid, original)
    msg = ask(h, tid, proposal)
    assert vote(h, tid, msg["id"]).json()["messages"][0]["agent"]["proposal_status"] == "APPLIED"
    assert c.post(f"/itineraries/{original['id']}/vote", headers=h).status_code == 200
    with SessionLocal() as db:
        selected, _ = proposals.published_plan(db, tid)
        assert selected.id == original["id"]
    again = ask(h, tid, proposal)
    assert again["agent"].get("validation_status") == "PASSED", again
    assert vote(h, tid, again["id"]).json()["messages"][0]["agent"]["proposal_status"] == "APPLIED"


def test_tc022_multiday_publication_preserves_unaffected_day(planning_adapter, monkeypatch):
    from app import config
    from .test_optimizer_integration import ready, generate, configuration
    from copy import deepcopy
    monkeypatch.setattr(config, "OPTIMIZER_SEARCH_SECONDS", 5)
    # This checks publication isolation, so provide a guaranteed feasible swap.
    # An unrestricted two-day optimum can use every day-one activity, leaving
    # only meals or an overlong festival as alternatives on Linux.
    activities = {"2026-11-06": {"bmuseum", "gwangalli", "shinsegae"},
                  "2026-11-07": {"gukje", "yacht", "cinema"}}
    meals = {"2026-11-06": "stew", "2026-11-07": "milmyeon"}
    def publication_routes(request, response):
        endpoints = set(request["points"])
        response["edges"] = [e for e in response["edges"]
            if {e["origin"], e["destination"]} <= activities[e["date"]] | {meals[e["date"]]} | endpoints]
        return response
    planning_adapter.transform = publication_routes
    h, tid, _ = ready(individual_method="feature_mean", days=("2026-11-06", "2026-11-07"))
    body = configuration(days=("2026-11-06", "2026-11-07"))
    body["duration_overrides"] = {pid: 60 for ids in activities.values() for pid in ids}
    for day in body["days"]:
        day["end_min"] = 810  # two activities plus lunch fit; a third activity does not
    response = generate(h, tid, body)
    assert response.status_code == 200, response.text
    plan = response.json()["plans"][0]
    with SessionLocal() as db:
        unaffected = deepcopy(db.get(Itinerary, plan["id"]).days[1])
    used = {i["poi_id"] for d in plan["days"] for i in d["items"]}
    spare = activities["2026-11-06"] - used
    assert len(spare) == 1, plan
    target = next(i for i in plan["days"][0]["items"] if i["poi_id"] in activities["2026-11-06"])
    proposal = {"date": "2026-11-06", "remove_item_ids": [f"2026-11-06@{target['start']}"],
                "add": [{"poi_id": spare.pop(), "start": target["start"]}]}
    msg = ask(h, tid, proposal)
    assert msg["agent"].get("validation_status") == "PASSED", msg
    voted = vote(h, tid, msg["id"])
    assert voted.json()["messages"][0]["agent"]["proposal_status"] == "APPLIED", voted.text
    after = current_plan(h, tid)
    with SessionLocal() as db:
        assert db.get(Itinerary, after["id"]).days[1] == unaffected
