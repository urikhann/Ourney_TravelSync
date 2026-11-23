"""Issue #19: TravelSync's suggestions change the plan only after the group approves them.

Approval rebuilds the plan with the real offline CP-SAT optimizer and the E3 check, like any plan.
"""
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app import services
from app.db import SessionLocal
from app.models import OptimizationRun
from app.routes import itineraries
from .test_flow import FEATURES, signup
from .test_optimizer_integration import c, generate

DAY = "2026-11-06"


def planned_trip(members=2):
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
    res = generate(heads[0], trip["id"])
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


def test_suggestion_waits_for_a_majority_then_rebuilds_a_checked_plan(planning_adapter):
    (a, b), tid, plan = planned_trip(2)
    stop, new, proposal = swap_for(tid, plan)
    msg = ask(b, tid, proposal)
    assert msg["agent"]["proposal_status"] == "PENDING"
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
    assert vote(a, tid, msg["id"], False).status_code == 409  # closed


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
    assert generate(a, tid).status_code == 200  # owner rebuilds the plan before anyone votes
    listed = c.get(f"/trips/{tid}/chat", headers=a).json()["messages"]
    assert next(m for m in listed if m["id"] == msg["id"])["agent"]["proposal_status"] == "STALE"
    r = vote(a, tid, msg["id"])
    assert r.status_code == 409 and r.json()["detail"]["status"] == "STALE"


def test_failed_rebuild_keeps_the_old_plan(planning_adapter, monkeypatch):
    (a,), tid, plan = planned_trip(1)
    _, _, proposal = swap_for(tid, plan)
    msg = ask(a, tid, proposal)

    async def infeasible(*args, **kwargs):
        raise HTTPException(409, "optimizer_infeasible")
    monkeypatch.setattr(itineraries, "_generate", infeasible)
    voted, note = vote(a, tid, msg["id"]).json()["messages"]
    assert voted["agent"]["proposal_status"] == "FAILED" and "optimizer_infeasible" in note["text"]
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
