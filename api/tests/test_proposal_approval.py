"""Issue #19: TravelSync's suggestions change the plan only after the group approves them.

Suggestions are validated before voting; approval applies the exact E3-checked draft.
"""
from uuid import uuid4
from datetime import datetime, timezone
import pytest

from sqlalchemy import select


from app import config, drive_planning, services
from app.db import SessionLocal
from app.models import OptimizationRun
from app.routes import recommendations
from .test_flow import FEATURES, signup
from .test_optimizer_integration import audits, c, configuration, generate

DAY = "2026-11-06"


@pytest.fixture(autouse=True)
def replacement_fixture_clock(monkeypatch):
    # Historical synthetic itineraries are future visits relative to this clock.
    from app import replacements
    monkeypatch.setattr(replacements, "current_time", lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
    original = recommendations.build_request
    def seeded(*args):
        request, members, version = original(*args)
        request["options"]["tie_seed"] = 0
        return request, members, version
    monkeypatch.setattr(recommendations, "build_request", seeded)


def planned_trip(members=2, *, transport_mode="walk"):
    """A trip with `members` people and one published plan."""
    heads = [signup("Ap" + uuid4().hex[:10]) for _ in range(members)]
    trip = c.post("/trips", headers=heads[0], json=dict(name="Approval", start_date=DAY, end_date=DAY)).json()
    for h in heads[1:]:
        assert c.post(f"/trips/join/{trip['invite_code']}", headers=h).status_code == 200
    for h in heads:
        c.put(f"/trips/{trip['id']}/preferences/me", headers=h, json={"ratings": dict.fromkeys(FEATURES, 3)})
        c.put(f"/trips/{trip['id']}/availability", headers=h, json={"dates": [DAY]})
    r = c.post(f"/trips/{trip['id']}/recommendations", headers=heads[0],
               json={"group_k": 3, "individual_method": "feature_mean"})
    assert r.status_code == 200, r.text
    res = generate(heads[0], trip["id"], {**configuration(), "transport_mode": transport_mode})
    assert res.status_code == 200, res.text
    return heads, trip["id"], res.json()["plans"][0]


def swap_for(tid, plan):
    """A realistic suggestion: replace one non-meal stop with another retrieved candidate."""
    items = plan["days"][0]["items"]
    stop = next(i for i in items if i.get("visit_category") == "activity" and not i.get("locked"))
    in_plan = {i["poi_id"] for i in items}
    with SessionLocal() as db:
        audit = db.scalar(select(OptimizationRun).where(OptimizationRun.trip_id == tid, OptimizationRun.status == "PASSED")
                          .order_by(OptimizationRun.id.desc()))
        candidates = audit.optimizer_request["candidates"]
    # like the agent's find_places: same kind of stop, not already in the plan
    new = next(r["poi_id"] for r in candidates if r["poi_id"] not in in_plan and r["visit_category"] == "activity")
    return stop, new, {"date": DAY, "remove_item_ids": [f"{DAY}@{stop['start']}"], "add": [{"poi_id": new, "start": stop["start"]}]}


def ask(h, tid, proposal):
    old = services.agent_turn

    async def fake(payload):
        return {"reply": "How about this swap?", "tool_calls": [], "proposal": proposal, "needs_clarification": False}
    services.agent_turn = fake
    try:
        r = c.post(f"/trips/{tid}/chat", headers=h, json={"text": "@TravelSync it's raining", "trigger": "MENTION"})
    finally:
        services.agent_turn = old
    assert r.status_code == 201, r.text
    return r.json()["messages"][1]


def vote(h, tid, mid, approve=True):
    return c.post(f"/trips/{tid}/chat/{mid}/vote", headers=h, json={"approve": approve})


def current_plan(h, tid):
    return c.get(f"/trips/{tid}/itineraries", headers=h).json()["plans"][0]


def test_validated_suggestion_waits_for_majority_then_applies_stored_plan(planning_adapter, monkeypatch):
    (a, b), tid, plan = planned_trip(2)
    stop, new, proposal = swap_for(tid, plan)
    msg = ask(b, tid, proposal)
    assert msg["agent"]["proposal_status"] == "PENDING"
    new = msg["agent"]["proposal"]["add"][0]["poi_id"]
    assert msg["agent"]["removed"] == [{"poi_id": stop["poi_id"], "start": stop["start"], "name": stop["place"]["name"]}]
    assert msg["agent"]["votes"] == {"approve": 0, "reject": 0, "needed": 2, "members": 2, "mine": None}

    r = vote(a, tid, msg["id"])  # 1 of 2: not a majority yet, nothing changes
    assert r.status_code == 200, r.text
    assert r.json()["messages"][0]["agent"]["proposal_status"] == "PENDING"
    assert r.json()["messages"][0]["agent"]["votes"]["mine"] is True
    assert current_plan(a, tid) == plan

    r = vote(b, tid, msg["id"])  # 2 of 2: applied
    assert r.status_code == 200, r.text
    voted, note = r.json()["messages"]
    assert voted["agent"]["proposal_status"] == "APPLIED", voted["agent"].get("result")
    assert note["kind"] == "agent" and note["reply_to"] == msg["id"] and "updated the plan" in note["text"]
    after = current_plan(a, tid)
    ids = {i["poi_id"] for i in after["days"][0]["items"]}
    assert new in ids and stop["poi_id"] not in ids
    kept = {i["poi_id"] for i in plan["days"][0]["items"]} - {stop["poi_id"]}
    assert voted["agent"]["result"]["mode"] == "rebuild" or kept <= ids  # "keep" kept every other stop
    print("MODE", voted["agent"]["result"]["mode"], "BEFORE", [(i["start"], i["poi_id"]) for i in plan["days"][0]["items"]])
    print("AFTER ", [(i["start"], i["poi_id"]) for i in after["days"][0]["items"]])
    with SessionLocal() as db:  # published through the normal E3-checked path
        audit = db.scalar(select(OptimizationRun).where(OptimizationRun.trip_id == tid).order_by(OptimizationRun.id.desc()))
        assert audit.status == "PASSED" and audit.itinerary_id == after["id"]
        assert stop["poi_id"] in audit.input["configuration"]["excluded_poi_ids"]
    assert vote(a, tid, msg["id"], False).json()["messages"][0]["agent"]["proposal_status"] == "APPLIED"  # idempotent

    for _ in range(11):
        assert c.post(f"/trips/{tid}/chat", headers=a, json={"text": "Thanks!"}).status_code == 201
    replacement = next(i for i in after["days"][0]["items"] if i["poi_id"] == new)

    async def explain(payload):
        assert all(m["id"] != note["id"] for m in payload["recent_messages"])
        assert payload["applied_changes"] == [{
            "date": DAY,
            "removed": [{"name": stop["place"]["name"], "start": stop["start"]}],
            "added": [{"name": replacement["place"]["name"], "start": replacement["start"]}],
            "day_rebuilt": voted["agent"]["result"]["mode"] == "rebuild",
        }]
        return {"reply": "The approved swap is in the plan.", "proposal": None}

    monkeypatch.setattr(services, "agent_turn", explain)
    response = c.post(f"/trips/{tid}/chat", headers=a, json={"text": "@TravelSync What changed?"})
    assert response.json()["messages"][1]["text"] == "The approved swap is in the plan."


def test_a_majority_of_rejections_closes_it_and_keeps_the_plan(planning_adapter):
    (a, b, x), tid, plan = planned_trip(3)
    _, _, proposal = swap_for(tid, plan)
    msg = ask(a, tid, proposal)
    assert vote(b, tid, msg["id"], False).json()["messages"][0]["agent"]["proposal_status"] == "PENDING"
    assert vote(b, tid, msg["id"], True).json()["messages"][0]["agent"]["votes"]["approve"] == 1  # changed mind
    assert vote(b, tid, msg["id"], False).status_code == 200
    voted, note = vote(x, tid, msg["id"], False).json()["messages"]  # 2 of 3 reject
    assert voted["agent"]["proposal_status"] == "REJECTED" and "rejected" in note["text"]
    assert current_plan(a, tid) == plan


def test_suggestion_goes_stale_when_the_plan_changes(planning_adapter):
    (a,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)
    msg = ask(a, tid, proposal)
    response = generate(a, tid)
    assert response.status_code == 200, response.text  # owner rebuilds before voting
    listed = c.get(f"/trips/{tid}/chat", headers=a).json()["messages"]
    assert next(m for m in listed if m["id"] == msg["id"])["agent"]["proposal_status"] == "STALE"
    r = vote(a, tid, msg["id"])
    assert r.status_code == 409 and r.json()["detail"]["status"] == "STALE"


def test_driving_approval_applies_draft_without_optimizer(planning_adapter, monkeypatch):
    leg_minutes = 10
    estimate_edges = drive_planning.draft_edges
    build_request = recommendations.build_request

    def seeded_recommendation_request(*args):
        request, snapshot, version = build_request(*args)
        request["options"]["tie_seed"] = 0
        return request, snapshot, version

    def fixture_draft_edges(request, context):
        # Isolate the approval fallback from route repair: the draft uses
        # the same travel times as this test's selected-leg provider.
        for edge in estimate_edges(request, context):
            yield edge.model_copy(update={"duration_minutes": leg_minutes})

    def selected_routes(request, response):
        legs = {(leg["date"], leg["origin"], leg["destination"]) for leg in request["route_legs"]}
        response["edges"] = [{**edge, "duration_minutes": leg_minutes} for edge in response["edges"]
                             if (edge["date"], edge["origin"], edge["destination"]) in legs]
        response["routing_attempts"] = request["routing_attempts"]
        return response

    planning_adapter.transform = selected_routes
    monkeypatch.setattr(recommendations, "build_request", seeded_recommendation_request)
    monkeypatch.setattr(drive_planning, "draft_edges", fixture_draft_edges)
    monkeypatch.setattr(config, "OPTIMIZER_SEARCH_SECONDS", 20)
    (owner,), trip_id, plan = planned_trip(1, transport_mode="drive")
    stop, replacement, proposal = swap_for(trip_id, plan)
    message = ask(owner, trip_id, proposal)
    async def forbidden_optimizer(*args, **kwargs):
        raise AssertionError("Approval must not optimize")

    monkeypatch.setattr(services, "build_plans", forbidden_optimizer)
    response = vote(owner, trip_id, message["id"])
    assert response.status_code == 200, response.text
    result = response.json()["messages"][0]["agent"]["result"]
    assert result["status"] == "APPLIED", result
    new = message["agent"]["proposal"]["add"][0]["poi_id"]
    rebuilt = current_plan(owner, trip_id)
    rebuilt_ids = {item["poi_id"] for item in rebuilt["days"][0]["items"]}
    assert new in rebuilt_ids and stop["poi_id"] not in rebuilt_ids
    assert {item["poi_id"] for item in plan["days"][0]["items"]} - {stop["poi_id"]} <= rebuilt_ids


def test_failed_validation_before_voting_keeps_the_old_plan(planning_adapter, monkeypatch):
    (a,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)

    def closed(req, response):
        for p in response["pois"]:
            for op in p["operations"].values():
                op["closed"]["value"] = True
        return response
    planning_adapter.transform = closed
    msg = ask(a, tid, proposal)
    assert msg["agent"]["proposal"] is None
    assert "proposal_status" not in msg["agent"]
    assert vote(a, tid, msg["id"]).status_code == 404
    assert current_plan(a, tid) == plan


def test_only_members_can_vote_and_only_on_suggestions(planning_adapter):
    (a,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)
    msg = ask(a, tid, proposal)
    outsider = signup("Out" + uuid4().hex[:8])
    assert vote(outsider, tid, msg["id"]).status_code == 404
    plain = c.post(f"/trips/{tid}/chat", headers=a, json={"text": "hi"}).json()["messages"][0]
    assert vote(a, tid, plain["id"]).status_code == 404
    assert c.post(f"/trips/{tid}/chat/{msg['id']}/vote", headers=a, json={"approve": "maybe"}).status_code == 422
