from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import replacements, services
from app.db import SessionLocal
from app.models import OptimizationRun, Poi
from .test_conversation_actions import post, mark_explicit
from .test_proposal_approval import planned_trip, current_plan, vote, replacement_fixture_clock
from .test_optimizer_integration import ready, generate, configuration


@pytest.fixture(autouse=True)
def restore_place_metadata():
    with SessionLocal() as db:
        saved = {p.id: (p.indoor, p.walking_level) for p in db.scalars(select(Poi))}
    yield
    with SessionLocal() as db:
        for p in db.scalars(select(Poi)):
            if p.id in saved:
                p.indoor, p.walking_level = saved[p.id]
        db.commit()


@pytest.mark.parametrize("preference,text", [
    ("indoor", "It's raining — please replan today's whole day to minimize outdoor activities."),
    ("less_walking", "We're tired — please replan today's whole day to minimize walking."),
])
def test_shortcut_uses_real_day_solver_and_vote(planning_adapter, replacement_fixture_clock, monkeypatch, preference, text):
    (h,), tid, original = planned_trip(1)
    locked = mark_explicit(tid, original)
    old_ids = {i["poi_id"] for d in original["days"] for i in d["items"]}
    with SessionLocal() as db:
        for p in db.scalars(select(Poi)):
            p.indoor = p.id not in old_ids
            p.walking_level = 1 if p.id in old_ids else 0
        db.commit()
    # Seoul Nov 6 at 05:00: the fixture's visits have not started yet.
    monkeypatch.setattr(replacements, "current_time", lambda: datetime(2026, 11, 5, 20, tzinfo=timezone.utc))
    async def forbidden(*args):
        raise AssertionError("Shortcuts must not depend on AI or a one-place replacement")
    monkeypatch.setattr(services, "agent_turn", forbidden)
    saved_before = current_plan(h, tid)
    answer = post(h, tid, "@TravelSync " + text)
    assert answer["agent"].get("validation_status") == "PASSED", answer
    assert answer["agent"]["action_type"] == "replan_day"
    assert answer["agent"]["affected_dates"] == ["2026-11-06"]
    assert current_plan(h, tid) == saved_before
    with SessionLocal() as db:
        draft = db.get(OptimizationRun, answer["agent"]["draft_id"])
        comfort = draft.input["replacement"]["comfort"]
        assert comfort["preference"] == preference and comfort["after"] < comfort["before"]
        kept = next(i for d in draft.response["days"] for i in d["items"] if i["poi_id"] == locked["poi_id"])
        assert kept["start_min"] == locked["start_min"]
    applied = vote(h, tid, answer["id"])
    assert applied.json()["messages"][0]["agent"]["proposal_status"] == "APPLIED", applied.text


def test_shortcut_requires_today_in_the_plan(planning_adapter, replacement_fixture_clock):
    (h,), tid, original = planned_trip(1)
    answer = post(h, tid, "@TravelSync We're tired — please make today's plan need less walking.")
    assert answer["agent"]["reason"] == "scope_date_required"
    assert answer["agent"]["proposal"] is None
    assert current_plan(h, tid) == original


def test_rain_with_no_improvement_keeps_plan(planning_adapter, replacement_fixture_clock, monkeypatch):
    (h,), tid, original = planned_trip(1)
    with SessionLocal() as db:
        for p in db.scalars(select(Poi)):
            p.indoor = True
        db.commit()
    monkeypatch.setattr(replacements, "current_time", lambda: datetime(2026, 11, 5, 20, tzinfo=timezone.utc))
    saved_before = current_plan(h, tid)
    answer = post(h, tid, "@TravelSync It's raining — can we swap outdoor stops for indoor places?")
    assert answer["agent"]["reason"] == "no_comfort_improvement", answer
    assert answer["agent"]["proposal"] is None
    assert current_plan(h, tid) == saved_before


def test_rain_preserves_other_dates(planning_adapter, replacement_fixture_clock, monkeypatch):
    dates = ("2026-11-06", "2026-11-07")
    h, tid, _ = ready(days=dates)
    response = generate(h, tid, configuration(days=dates))
    assert response.status_code == 200, response.text
    original = response.json()["plans"][0]
    used = {i["poi_id"] for d in original["days"] for i in d["items"]}
    with SessionLocal() as db:
        for p in db.scalars(select(Poi)):
            p.indoor = p.id not in used
        db.commit()
    monkeypatch.setattr(replacements, "current_time", lambda: datetime(2026, 11, 5, 20, tzinfo=timezone.utc))
    answer = post(h, tid, "@TravelSync It's raining — please replan today's whole day to minimize outdoor activities.")
    assert answer["agent"].get("validation_status") == "PASSED", answer
    with SessionLocal() as db:
        draft = db.get(OptimizationRun, answer["agent"]["draft_id"])
        assert draft.response["days"][1] == replacements.plans([original["days"][1]])[0].model_dump(mode="json")
