from types import SimpleNamespace

import pytest

from app import conversation, planning_actions
from .test_conversation_actions import post
from .test_optimizer_integration import c
from .test_proposal_approval import planned_trip, current_plan, replacement_fixture_clock


@pytest.mark.parametrize("count", [1, 2, 3])
def test_date_choices_include_exactly_the_planned_dates(count):
    dates = [f"2026-11-{day:02}" for day in range(6, 6 + count)]
    plan = SimpleNamespace(days=[dict(date=day) for day in reversed(dates)])
    action = dict(action_type="replan_day", date="today", constraints={"day_preference": "less_walking"})
    answer = conversation.date_question(dict(reason="scope_date_required"), plan,
        SimpleNamespace(id=12), SimpleNamespace(text="Less walking", author_id="owner"), action)
    assert answer["date_selection"]["dates"] == dates
    assert answer["date_selection"]["action"] == action


@pytest.mark.parametrize("preference,text", [
    ("less_walking", "We're tired — please make today's plan need less walking."),
    ("indoor", "It's raining — can we swap outdoor stops for indoor places?"),
])
def test_date_button_resumes_original_action_once(planning_adapter, replacement_fixture_clock, monkeypatch, preference, text):
    (owner, other), tid, original = planned_trip(2)
    question = post(owner, tid, "@TravelSync " + text)
    assert question["agent"]["date_selection"]["dates"] == ["2026-11-06"]
    captured = []

    async def execute(db, trip, message, plan, audit, action):
        captured.append(action)
        return dict(state="SUCCEEDED", reply="Draft requested", proposal=None, tool_calls=[])

    monkeypatch.setattr(planning_actions, "execute", execute)
    denied = post(other, tid, "@TravelSync 2026-11-06", reply_to=question["id"], trigger="QUICK_ACTION")
    assert denied["agent"]["reason"] == "no_pending_action"
    invalid = post(owner, tid, "@TravelSync 2026-11-09", reply_to=question["id"], trigger="QUICK_ACTION")
    assert invalid["agent"]["reason"] == "scope_date_required"
    assert captured == []
    answer = post(owner, tid, "@TravelSync 2026-11-06", reply_to=question["id"], trigger="QUICK_ACTION")
    assert answer["agent"]["state"] == "SUCCEEDED"
    assert captured == [dict(action_type="replan_day", date="2026-11-06", constraints={"day_preference": preference})]
    history = c.get(f"/trips/{tid}/chat", headers=owner).json()["messages"]
    saved = next(m for m in history if m["id"] == question["id"])
    assert saved["agent"]["date_selection"]["status"] == "CONSUMED"
    duplicate = post(owner, tid, "@TravelSync 2026-11-06", reply_to=question["id"], trigger="QUICK_ACTION")
    assert duplicate["agent"]["reason"] == "pending_action_consumed"
    assert len(captured) == 1
    assert current_plan(owner, tid) == original
