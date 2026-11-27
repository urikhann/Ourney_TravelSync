"""Identical schedules explain the outcome instead of opening a meaningless vote."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from app import planning_actions, replacements, services
from app.db import SessionLocal
from app.models import ChatMessage, OptimizationRun
from .test_conversation_actions import post
from .test_proposal_approval import planned_trip, current_plan, vote, replacement_fixture_clock
from .test_optimizer_integration import c


@pytest.mark.parametrize("korean", [False, True])
def test_confirmation_with_unchanged_schedule_does_not_offer_vote(planning_adapter, replacement_fixture_clock, monkeypatch, korean):
    (h,), tid, original = planned_trip(1)
    day = original["days"][0]["date"]
    async def model(payload):
        return dict(reply="다시 계획할까요?" if korean else "Shall I replan that day?", proposal=None, tool_calls=[],
                    pending_action=dict(action_type="replan_day", date=day, constraints={}))
    monkeypatch.setattr(services, "agent_turn", model)
    question = post(h, tid, "@TravelSync 도와줘" if korean else "@TravelSync help me plan")
    async def same_schedule(db, trip, message, plan, audit, req, original_days, action, protected, rows, **kwargs):
        return req, original_days, {}, {day}
    monkeypatch.setattr(planning_actions, "scoped_solve", same_schedule)
    answer = post(h, tid, "@TravelSync 네" if korean else "@TravelSync Yes", reply_to=question["id"])
    assert answer["agent"]["reason"] == "no_itinerary_changes", answer
    assert answer["agent"]["state"] == "SUCCEEDED"
    assert answer["agent"]["proposal"] is None
    assert "proposal_status" not in answer["agent"] and "draft_id" not in answer["agent"]
    assert ("투표할 변경 사항이 없습니다" if korean else "nothing to vote on") in answer["text"]
    assert current_plan(h, tid) == original
    assert vote(h, tid, answer["id"]).status_code == 404
    with SessionLocal() as db:
        assert not db.scalars(select(OptimizationRun).where(OptimizationRun.trip_id == tid,
                                                           OptimizationRun.status == "VALIDATED")).all()


def test_existing_unchanged_pending_proposal_is_explanatory_and_cannot_be_voted(planning_adapter, replacement_fixture_clock):
    (h,), tid, original = planned_trip(1)
    with SessionLocal() as db:
        base = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == original["id"]))
        draft = OptimizationRun(trip_id=tid, recommendation_run_id=base.recommendation_run_id,
            requested_by=base.requested_by, status="VALIDATED",
            input={**deepcopy(base.input), "replacement": {"base_run_id": base.id,
                "base_itinerary_id": original["id"], "affected_dates": [original["days"][0]["date"]]}},
            optimizer_request=deepcopy(base.optimizer_request), response=deepcopy(base.response),
            planning_data=deepcopy(base.planning_data))
        db.add(draft)
        db.flush()
        message = ChatMessage(trip_id=tid, kind="agent", text="I validated a draft. Please vote.",
            agent=dict(reply="I validated a draft. Please vote.", proposal_status="PENDING", state="SUCCEEDED",
                action_type="replan_day", plan_run_id=base.id, draft_id=draft.id, tool_calls=[],
                proposal=dict(date=original["days"][0]["date"], remove_item_ids=[], add=[])))
        db.add(message)
        db.commit()
        message_id = message.id
    response = c.get(f"/trips/{tid}/chat", headers=h)
    shown = next(m for m in response.json()["messages"] if m["id"] == message_id)
    assert shown["agent"]["proposal"] is None
    assert "nothing to vote on" in shown["text"]
    assert vote(h, tid, message_id).status_code == 409
    assert current_plan(h, tid) == original


def test_schedule_comparison_ignores_metadata_but_detects_real_changes(planning_adapter, replacement_fixture_clock):
    (h,), tid, original = planned_trip(1)
    days = replacements.plans(original["days"])
    raw = [d.model_dump(mode="json") for d in days]
    raw[0]["items"][0]["visit_cost"] += 1
    assert not replacements.itinerary_changed(days, replacements.plans(raw))
    raw[0]["items"][0]["poi_id"] = "a-different-place"
    assert replacements.itinerary_changed(days, replacements.plans(raw))
    raw = [d.model_dump(mode="json") for d in days]
    raw[0]["items"][0]["start_min"] += 1
    assert replacements.itinerary_changed(days, replacements.plans(raw))
