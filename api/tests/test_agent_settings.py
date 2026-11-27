from copy import deepcopy
from datetime import datetime

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Itinerary, OptimizationRun, Poi
from app import replacements, services
from .test_conversation_actions import post
from .test_optimizer_integration import c
from .test_proposal_approval import planned_trip, current_plan, vote, replacement_fixture_clock


def target(plan):
    item = next(i for d in plan["days"] for i in d["items"] if i["visit_category"] == "activity")
    return item, item["place"]["name"]


def confirm(headers, tid, question):
    return post(headers, tid, "@TravelSync Yes", reply_to=question["id"])


def test_chat_lock_and_unlock_share_owner_controls(compact_planning_adapter, replacement_fixture_clock):
    (owner,), tid, original = planned_trip(1)
    item, name = target(original)
    before = current_plan(owner, tid)
    question = post(owner, tid, "@TravelSync Lock " + name)
    assert question["agent"]["pending_action"]["action"]["locked"] is True
    assert current_plan(owner, tid) == before
    answer = confirm(owner, tid, question)
    assert answer["agent"]["state"] == "SUCCEEDED", answer
    locked = current_plan(owner, tid)
    assert locked["id"] == original["id"]
    assert next(i for d in locked["days"] for i in d["items"] if i["poi_id"] == item["poi_id"])["locked"]
    assert locked["lock_revision"] == 1
    duplicate = confirm(owner, tid, question)
    assert duplicate["agent"]["reason"] == "pending_action_consumed"
    question = post(owner, tid, "@TravelSync Unlock " + name)
    assert confirm(owner, tid, question)["agent"]["state"] == "SUCCEEDED"
    after = current_plan(owner, tid)
    assert not next(i for d in after["days"] for i in d["items"] if i["poi_id"] == item["poi_id"])["locked"]
    assert after["lock_revision"] == 2
    assert [(i["poi_id"], i["start"], i["end"]) for d in before["days"] for i in d["items"]] == [
        (i["poi_id"], i["start"], i["end"]) for d in after["days"] for i in d["items"]]


@pytest.mark.parametrize("language", ["en", "ko"])
@pytest.mark.parametrize("direction", ["shorter", "longer"])
def test_duration_change_is_validated_voted_and_preserves_original(compact_planning_adapter, replacement_fixture_clock, language, direction):
    (owner,), tid, original = planned_trip(1)
    item, name = target(original)
    minutes = 30 if direction == "shorter" else item["stay_minutes"] + 30
    saved = current_plan(owner, tid)
    with SessionLocal() as db:
        saved_days = deepcopy(db.get(Itinerary, original["id"]).days)
    text = f"Set the visit duration for {name} to {minutes} minutes" if language == "en" else f"{name} 방문 시간을 {minutes}분으로 바꿔줘"
    question = post(owner, tid, "@TravelSync " + text, language=language)
    assert question["agent"]["pending_action"]["action"]["duration_min"] == minutes, question
    assert current_plan(owner, tid) == saved
    answer = confirm(owner, tid, question)
    agent = answer["agent"]
    assert agent.get("validation_status") == "PASSED", answer
    assert agent["action_type"] == "set_visit_duration"
    assert agent["proposal"]["remove_item_ids"] == [] and agent["proposal"]["add"] == []
    assert agent["proposal_details"]["adjusted"]
    assert current_plan(owner, tid) == saved
    result = vote(owner, tid, answer["id"])
    assert result.json()["messages"][0]["agent"]["proposal_status"] == "APPLIED", result.text
    changed = current_plan(owner, tid)
    visits = {i["poi_id"]: i for d in changed["days"] for i in d["items"]}
    assert visits[item["poi_id"]]["stay_minutes"] == minutes
    assert visits[item["poi_id"]]["duration_source"] == "user-duration-override"
    assert set(visits) == {i["poi_id"] for d in original["days"] for i in d["items"]}
    with SessionLocal() as db:
        assert db.get(Itinerary, original["id"]).days == saved_days
        audit = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == changed["id"]))
        assert audit.input["configuration"]["duration_overrides"][item["poi_id"]] == minutes


@pytest.mark.parametrize("setting", ["Lock {name}", "Set the visit duration for {name} to 30 minutes"])
def test_other_members_cannot_change_owner_settings(compact_planning_adapter, replacement_fixture_clock, setting):
    (owner, member), tid, original = planned_trip(2)
    _, name = target(original)
    answer = post(member, tid, "@TravelSync " + setting.format(name=name))
    assert answer["agent"]["reason"] == "settings_owner_required"
    assert "pending_action" not in answer["agent"]
    assert current_plan(owner, tid) == original


@pytest.mark.parametrize("flag", ["completed", "reservation_confirmed"])
def test_protected_visits_cannot_be_edited(compact_planning_adapter, replacement_fixture_clock, flag):
    (owner,), tid, original = planned_trip(1)
    item, name = target(original)
    with SessionLocal() as db:
        plan = db.get(Itinerary, original["id"])
        days = deepcopy(plan.days)
        next(i for d in days for i in d["items"] if i["poi_id"] == item["poi_id"])[flag] = True
        plan.days = days
        db.commit()
    for text in ("Unlock " + name, f"Set the visit duration for {name} to 30 minutes"):
        answer = post(owner, tid, "@TravelSync " + text)
        assert answer["agent"]["reason"] == "place_lock_protected"


def test_infeasible_duration_keeps_current_plan(compact_planning_adapter, replacement_fixture_clock):
    (owner,), tid, original = planned_trip(1)
    _, name = target(original)
    question = post(owner, tid, f"@TravelSync Set the visit duration for {name} to 1440 minutes")
    answer = confirm(owner, tid, question)
    assert answer["agent"]["proposal"] is None
    assert answer["agent"]["state"] == "FAILED", answer
    assert current_plan(owner, tid) == original


def test_model_setting_request_uses_the_same_confirmation_gate(compact_planning_adapter, replacement_fixture_clock, monkeypatch):
    (owner,), tid, original = planned_trip(1)
    item, _ = target(original)
    item_id = f"{original['days'][0]['date']}@{item['start']}"

    async def model(payload):
        return dict(reply="", proposal=None, tool_calls=[], pending_action=dict(
            action_type="set_place_lock", target_item_id=item_id, locked=True))

    monkeypatch.setattr(services, "agent_turn", model)
    question = post(owner, tid, "@TravelSync Keep this stop fixed for us")
    assert question["agent"]["pending_action"]["action"]["target_item_id"] == item_id
    assert confirm(owner, tid, question)["agent"]["state"] == "SUCCEEDED"
    assert next(i for d in current_plan(owner, tid)["days"] for i in d["items"] if i["poi_id"] == item["poi_id"])["locked"]


def test_lock_confirmation_becomes_stale_after_another_lock_edit(compact_planning_adapter, replacement_fixture_clock):
    (owner,), tid, original = planned_trip(1)
    item, name = target(original)
    question = post(owner, tid, "@TravelSync Lock " + name)
    changed = c.post(f"/trips/{tid}/itineraries/{original['id']}/lock", headers=owner,
        json=dict(item_id=f"{original['days'][0]['date']}@{item['start']}", locked=True, expected_revision=0))
    assert changed.status_code == 200, changed.text
    answer = confirm(owner, tid, question)
    assert answer["agent"]["reason"] == "pending_action_stale"
    assert current_plan(owner, tid)["lock_revision"] == 1


def test_locked_target_that_starts_during_voting_cannot_change_duration(compact_planning_adapter, replacement_fixture_clock, monkeypatch):
    (owner,), tid, original = planned_trip(1)
    item, name = target(original)
    day = original["days"][0]["date"]
    result = c.post(f"/trips/{tid}/itineraries/{original['id']}/lock", headers=owner,
        json=dict(item_id=f"{day}@{item['start']}", locked=True, expected_revision=0))
    assert result.status_code == 200, result.text
    question = post(owner, tid, f"@TravelSync Set the visit duration for {name} to 30 minutes")
    answer = confirm(owner, tid, question)
    assert answer["agent"].get("validation_status") == "PASSED", answer
    monkeypatch.setattr(replacements, "current_time", lambda: datetime.fromisoformat(f"{day}T{item['start']}:00+09:00"))
    applied = vote(owner, tid, answer["id"])
    agent = applied.json()["messages"][0]["agent"]
    assert agent["proposal_status"] == "STALE"
    unchanged = current_plan(owner, tid)
    assert unchanged["id"] == original["id"]
    assert next(i for d in unchanged["days"] for i in d["items"] if i["poi_id"] == item["poi_id"])["stay_minutes"] == item["stay_minutes"]


def test_ambiguous_name_does_not_choose_a_place(compact_planning_adapter, replacement_fixture_clock):
    (owner,), tid, original = planned_trip(1)
    pids = [i["poi_id"] for d in original["days"] for i in d["items"]][:2]
    with SessionLocal() as db:
        rows = db.scalars(select(Poi).where(Poi.id.in_(pids))).all()
        saved = [(p.id, p.dataset_version, p.name) for p in rows]
        for p in rows:
            p.name = "Shared place name"
        db.commit()
    try:
        answer = post(owner, tid, "@TravelSync Lock Shared place name")
        assert answer["agent"]["reason"] == "ambiguous_target_poi"
        assert "pending_action" not in answer["agent"]
    finally:
        with SessionLocal() as db:
            for pid, version, name in saved:
                db.get(Poi, (pid, version)).name = name
            db.commit()
