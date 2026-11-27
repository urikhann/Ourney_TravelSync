"""Structured confirmation/scopes. Disposable DB, synthetic hours/routes, real E3."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import conversation, planning_actions, replacements, services, config
from app.db import SessionLocal
from app.models import ChatMessage, Itinerary, OptimizationRun
from .test_proposal_approval import planned_trip, swap_for, vote, current_plan, replacement_fixture_clock
from .test_optimizer_integration import c


def post(h, tid, text, **extra):
    response = c.post(f"/trips/{tid}/chat", headers=h, json=dict(text=text, **extra))
    assert response.status_code == 201, response.text
    return response.json()["messages"][-1]


def ask_confirmation(monkeypatch, h, tid, plan):
    stop, _, _ = swap_for(tid, plan)
    async def model(payload):
        return dict(reply="Shall I find a replacement for that activity?", proposal=None,
            pending_action=dict(action_type="replace_poi", target_item_id=f"{plan['days'][0]['date']}@{stop['start']}", constraints={}), tool_calls=[])
    monkeypatch.setattr(services, "agent_turn", model)
    return post(h, tid, "@TravelSync Can you help with this activity?")


@pytest.mark.parametrize("yes", ["Yes", "네", "예", "응", "좋아요"])
def test_confirmation_executes_same_pipeline_without_model_or_vote(planning_adapter, replacement_fixture_clock, monkeypatch, yes):
    (h, other), tid, original = planned_trip(2)
    question = ask_confirmation(monkeypatch, h, tid, original)
    envelope = question["agent"]["pending_action"]
    assert envelope["question_message_id"] == question["id"] and envelope["group_id"] == tid
    assert envelope["target_itinerary_item_id"] and envelope["permissions"]["can_prepare"]
    async def forbidden(*a):
        raise AssertionError("confirmation must not infer intent again")
    monkeypatch.setattr(services, "agent_turn", forbidden)
    answer = post(h, tid, "@TravelSync "+yes)
    assert answer["agent"].get("validation_status") == "PASSED", answer
    assert answer["agent"]["proposal_status"] == "PENDING"
    assert answer["agent"]["votes"]["approve"] == 0
    assert current_plan(h, tid) == original
    with SessionLocal() as db:
        assert db.get(ChatMessage, question["id"]).agent["pending_action"]["status"] == "CONSUMED"
    assert vote(other, tid, answer["id"]).json()["messages"][0]["agent"]["proposal_status"] == "PENDING"


def test_negative_and_unassociated_confirmation(planning_adapter, replacement_fixture_clock, monkeypatch):
    (h,), tid, original = planned_trip(1)
    no_context = post(h, tid, "@TravelSync Yes")
    assert no_context["agent"]["reason"] == "no_pending_action"
    question = ask_confirmation(monkeypatch, h, tid, original)
    answer = post(h, tid, "아니요", reply_to=question["id"])
    assert answer["agent"]["reason"] == "pending_action_declined" and current_plan(h, tid) == original
    repeated = post(h, tid, "@TravelSync Yes", reply_to=question["id"])
    assert repeated["agent"]["reason"] == "pending_action_consumed"


def test_multiple_actions_and_other_user_are_isolated(planning_adapter, replacement_fixture_clock, monkeypatch):
    (h, other), tid, original = planned_trip(2)
    first = ask_confirmation(monkeypatch, h, tid, original)
    second = ask_confirmation(monkeypatch, h, tid, original)
    other_attempt = post(other, tid, "@TravelSync Yes", reply_to=first["id"])
    assert other_attempt["agent"]["reason"] == "no_pending_action"
    ambiguous = post(h, tid, "@TravelSync Yes")
    assert ambiguous["agent"]["reason"] == "ambiguous_pending_action"
    selected = post(h, tid, "@TravelSync Yes", reply_to=second["id"])
    assert selected["agent"].get("validation_status") == "PASSED", selected
    with SessionLocal() as db:
        assert db.get(ChatMessage, first["id"]).agent["pending_action"]["status"] == "PENDING"


@pytest.mark.parametrize("change", ["expiry", "itinerary", "constraints"])
def test_expired_or_stale_confirmation(planning_adapter, replacement_fixture_clock, monkeypatch, change):
    (h,), tid, original = planned_trip(1)
    question = ask_confirmation(monkeypatch, h, tid, original)
    if change == "expiry":
        now = replacements.current_time()
        monkeypatch.setattr(replacements, "current_time", lambda: now+timedelta(minutes=10))
    elif change == "constraints":
        c.put(f"/trips/{tid}/availability", headers=h, json={"dates": []})
    else:
        with SessionLocal() as db:
            plan = db.get(Itinerary, original["id"])
            raw = deepcopy(plan.days)
            raw[0]["items"][0]["lock_reason"] = "explicit_lock"
            raw[0]["items"][0]["locked"] = True
            plan.days = raw
            db.commit()
    answer = post(h, tid, "@TravelSync Yes", reply_to=question["id"])
    assert answer["agent"]["reason"] == ("pending_action_expired" if change == "expiry" else "pending_action_stale")
    assert answer["agent"]["proposal"] is None


def mark_explicit(tid, plan):
    stop = plan["days"][0]["items"][0]
    with SessionLocal() as db:
        saved = db.get(Itinerary, plan["id"])
        raw = deepcopy(saved.days)
        raw[0]["items"][0].update(locked=True, lock_reason="explicit_lock")
        saved.days = raw
        audit = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == saved.id))
        data = deepcopy(audit.input)
        data["configuration"]["locked_items"] = [dict(poi_id=stop["poi_id"], date=raw[0]["date"], start_min=stop["start_min"])]
        audit.input = data
        db.commit()
    return stop


def test_full_replan_keeps_scope_and_protected_alternative(planning_adapter, replacement_fixture_clock, monkeypatch):
    (h,), tid, original = planned_trip(1)
    stop = mark_explicit(tid, original)
    question = post(h, tid, "@TravelSync replan from scratch again.")
    assert question["agent"]["pending_action"]["action_type"] == "replan_unlocked"
    assert "single" not in question["text"] and question["agent"]["proposal"] is None
    confirmed = post(h, tid, "@TravelSync Yes", reply_to=question["id"])
    assert confirmed["agent"].get("validation_status") == "PASSED", confirmed
    assert confirmed["agent"]["action_type"] == "replan_unlocked"
    voted = vote(h, tid, confirmed["id"])
    assert voted.json()["messages"][0]["agent"]["proposal_status"] == "APPLIED", voted.text
    after = current_plan(h, tid)
    kept = next(i for d in after["days"] for i in d["items"] if i["poi_id"] == stop["poi_id"])
    assert kept["start"] == stop["start"] and kept["locked"]


def test_full_replan_unlocked_is_not_single_swap(planning_adapter, replacement_fixture_clock, monkeypatch):
    (h,), tid, original = planned_trip(1)
    async def forbidden(*a):
        raise AssertionError("No model explanation is required after a validated draft")
    monkeypatch.setattr(services, "agent_turn", forbidden)
    answer = post(h, tid, "@TravelSync replan from scratch again.")
    with SessionLocal() as db:
        work = db.scalar(select(OptimizationRun).where(OptimizationRun.trip_id == tid,
            OptimizationRun.status == "PREVIEWED").order_by(OptimizationRun.id.desc()))
        assert work.input["action_type"] == "replan_all"
        assert work.response["validation_status"] == "PASSED"
    if answer["agent"].get("reason") == "no_itinerary_changes":
        assert answer["agent"]["proposal"] is None and answer["agent"]["state"] == "SUCCEEDED"
        assert current_plan(h, tid) == original
        assert vote(h, tid, answer["id"]).status_code == 404
        return
    assert answer["agent"].get("validation_status") == "PASSED", answer
    assert answer["agent"]["action_type"] == "replan_all"
    assert current_plan(h, tid) == original
    assert vote(h, tid, answer["id"]).json()["messages"][0]["agent"]["proposal_status"] == "APPLIED"


def test_approved_ordinary_items_are_editable(planning_adapter, replacement_fixture_clock, monkeypatch):
    (h,), tid, original = planned_trip(1)
    question = ask_confirmation(monkeypatch, h, tid, original)
    answer = post(h, tid, "@TravelSync Yes", reply_to=question["id"])
    assert vote(h, tid, answer["id"]).json()["messages"][0]["agent"]["proposal_status"] == "APPLIED"
    after = current_plan(h, tid)
    assert not any(i["locked"] for d in after["days"] for i in d["items"])
    again = post(h, tid, "@TravelSync replan all")
    assert again["agent"]["action_type"] == "replan_all" and again["agent"].get("validation_status") == "PASSED", again


def test_verified_hours_then_solver_failure_is_not_ai_unavailable(planning_adapter, replacement_fixture_clock, monkeypatch):
    (h,), tid, original = planned_trip(1)
    async def failed(payload):
        return dict(run_id=payload["run_id"], input_snapshot=payload, solver_status="INFEASIBLE", error_code="NO_FEASIBLE_ITINERARY")
    monkeypatch.setattr(services, "build_plans", failed)
    answer = post(h, tid, "@TravelSync replan all")
    assert answer["agent"]["reason"] == "optimizer_no_feasible_itinerary"
    assert "can't answer" not in answer["text"]
    assert current_plan(h, tid) == original


def test_replan_day_does_not_touch_other_date(planning_adapter, replacement_fixture_clock, monkeypatch):
    from .test_optimizer_integration import ready, generate, configuration
    monkeypatch.setattr(config, "OPTIMIZER_SEARCH_SECONDS", 5)
    dates = ("2026-11-06", "2026-11-07")
    h, tid, _ = ready(individual_method="feature_mean", days=dates)
    response = generate(h, tid, configuration(days=dates))
    assert response.status_code == 200, response.text
    original = response.json()["plans"][0]
    with SessionLocal() as db:
        other = deepcopy(db.get(Itinerary, original["id"]).days[1])
    answer = post(h, tid, "@TravelSync replan day 2026-11-06")
    assert answer["agent"].get("validation_status") == "PASSED", answer
    assert answer["agent"]["affected_dates"] == [dates[0]]
    assert vote(h, tid, answer["id"]).json()["messages"][0]["agent"]["proposal_status"] == "APPLIED"
    with SessionLocal() as db:
        assert db.get(Itinerary, current_plan(h, tid)["id"]).days[1] == other


def test_legacy_retention_lock_provenance_preserves_explicit_lock(planning_adapter, replacement_fixture_clock):
    from .test_proposal_approval import ask
    from app.lock_policy import describe
    (h,), tid, original = planned_trip(1)
    explicit = mark_explicit(tid, original)
    current = current_plan(h, tid)
    _, _, proposal = swap_for(tid, current)
    message = ask(h, tid, proposal)
    assert vote(h, tid, message["id"]).json()["messages"][0]["agent"]["proposal_status"] == "APPLIED"
    with SessionLocal() as db:
        msg = db.get(ChatMessage, message["id"])
        agent = dict(msg.agent)
        child = db.get(OptimizationRun, agent["result"]["plan_run_id"])
        parent = db.get(OptimizationRun, agent["plan_run_id"])
        raw = deepcopy(child.input)
        raw.pop("lock_provenance")
        locks = {l["poi_id"]: l for l in parent.input["configuration"]["locked_items"]}
        locks.update({a["poi_id"]: dict(poi_id=a["poi_id"], date=agent["proposal"]["date"]) for a in agent["proposal"]["add"]})
        removed = {x["poi_id"] for x in agent["removed"]}
        for d in parent.response["days"]:
            for v in d["items"]:
                if v["poi_id"] not in removed:
                    locks.setdefault(v["poi_id"], dict(poi_id=v["poi_id"], date=d["date"]))
        # Real GenerateInput JSON includes null start_min on date-only locks.
        raw["configuration"]["locked_items"] = [{**lock, "start_min": lock.get("start_min")} for lock in locks.values()]
        child.input = raw
        agent.pop("draft_id")
        msg.agent = agent
        saved = db.get(Itinerary, child.itinerary_id)
        legacy = deepcopy(saved.days)
        for d in legacy:
            for v in d["items"]:
                v["locked"] = True
        saved.days = legacy
        db.commit()
        info = describe(saved, child, db, replacements.current_time())
        assert sum(v["locked"] for v in info.values()) == 1
        assert info[f"{legacy[0]['date']}@{explicit['start']}"]["locked"]
        assert all(v["locked"] for d in saved.days for v in d["items"]), "classification must not rewrite historical records"


def test_reservations_and_unknown_legacy_locks_remain_protected(replacement_fixture_clock):
    from types import SimpleNamespace
    from app.lock_policy import describe
    plan = SimpleNamespace(days=[dict(date="2026-11-06", items=[
        dict(poi_id="reservation", start="10:00", locked=False, reservation_confirmed=True),
        dict(poi_id="unknown", start="12:00", locked=True),
        dict(poi_id="ordinary", start="14:00", locked=False)])])
    info = describe(plan, SimpleNamespace(input={}, trip_id="synthetic"), None, replacements.current_time())
    assert info["2026-11-06@10:00"]["lock_reason"] == "confirmed_reservation"
    assert info["2026-11-06@12:00"]["lock_reason"] == "legacy_lock_unverified"
    assert not info["2026-11-06@14:00"]["locked"]


def test_explicit_remove_and_add_use_existing_validators(planning_adapter, replacement_fixture_clock):
    (h,), tid, original = planned_trip(1)
    # A cafe can be removed without relaxing mandatory meal/activity composition.
    cafe = next(v for d in original["days"] for v in d["items"] if v["visit_category"] == "cafe")
    removed = post(h, tid, f"@TravelSync remove {cafe['place']['name']}")
    assert removed["agent"].get("validation_status") == "PASSED", removed
    assert vote(h, tid, removed["id"]).json()["messages"][0]["agent"]["proposal_status"] == "APPLIED"
    restored = post(h, tid, f"@TravelSync add {cafe['place']['name']}")
    assert restored["agent"].get("validation_status") == "PASSED", restored
    assert restored["agent"]["action_type"] == "add_poi"


def test_missing_final_model_text_does_not_discard_proposal(planning_adapter, replacement_fixture_clock, monkeypatch):
    (h,), tid, original = planned_trip(1)
    _, _, proposal = swap_for(tid, original)
    async def model(payload):
        return dict(reply="", proposal=proposal, tool_calls=[dict(name="check_hours", summary="Opening hours verified")],
                    explanation_error="llm_request_timeout")
    monkeypatch.setattr(services, "agent_turn", model)
    answer = post(h, tid, "@TravelSync Find an alternative indoor activity")
    assert answer["agent"].get("validation_status") == "PASSED", answer
    assert "validated" in answer["text"] and answer["agent"]["proposal_status"] == "PENDING"


def test_concurrent_confirmation_claim_executes_once(planning_adapter, replacement_fixture_clock, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    (h,), tid, original = planned_trip(1)
    question = ask_confirmation(monkeypatch, h, tid, original)
    with ThreadPoolExecutor(max_workers=2) as executor:
        answers = list(executor.map(lambda _: post(h, tid, "@TravelSync Yes", reply_to=question["id"]), range(2)))
    assert sum(a["agent"].get("validation_status") == "PASSED" for a in answers) == 1
    assert any(a["agent"].get("reason") == "pending_action_consumed" for a in answers)
    assert current_plan(h, tid) == original


def test_driving_replan_reuses_verified_selected_routes(planning_adapter, replacement_fixture_clock, monkeypatch):
    def only_selected(request, response):
        legs = {(x["date"], x["origin"], x["destination"]) for x in request.get("route_legs", [])}
        response["edges"] = [e for e in response["edges"] if (e["date"], e["origin"], e["destination"]) in legs]
        response["routing_attempts"] = request.get("routing_attempts", {})
        return response
    planning_adapter.transform = only_selected
    monkeypatch.setattr(config, "OPTIMIZER_SEARCH_SECONDS", 10)
    (h,), tid, original = planned_trip(1, transport_mode="drive")
    answer = post(h, tid, "@TravelSync replan all")
    with SessionLocal() as db:
        work = db.scalar(select(OptimizationRun).where(OptimizationRun.trip_id == tid,
            OptimizationRun.status == "PREVIEWED").order_by(OptimizationRun.id.desc()))
        assert work.response["validation_status"] == "PASSED"
        assert work.optimizer_request["edges"]
        assert all(e["status"] == "KNOWN" for e in work.optimizer_request["edges"])
    if answer["agent"].get("reason") == "no_itinerary_changes":
        assert answer["agent"]["proposal"] is None and current_plan(h, tid) == original
        assert vote(h, tid, answer["id"]).status_code == 404
        return
    assert answer["agent"].get("validation_status") == "PASSED", answer
    assert vote(h, tid, answer["id"]).json()["messages"][0]["agent"]["proposal_status"] == "APPLIED"


def test_unstructured_model_confirmation_is_not_a_hidden_action(planning_adapter, replacement_fixture_clock, monkeypatch):
    (h,), tid, original = planned_trip(1)
    async def unstructured(payload):
        return dict(reply="Would you like me to find a replacement?", proposal=None, tool_calls=[])
    monkeypatch.setattr(services, "agent_turn", unstructured)
    answer = post(h, tid, "@TravelSync Could you help me?")
    assert answer["agent"]["reason"] == "unstructured_confirmation"
    assert "pending_action" not in answer["agent"]
    assert post(h, tid, "@TravelSync Yes")["agent"]["reason"] == "no_pending_action"


def test_replanning_preserves_existing_owner_authorization(planning_adapter, replacement_fixture_clock):
    (owner, member), tid, original = planned_trip(2)
    answer = post(member, tid, "@TravelSync replan all")
    assert answer["agent"]["reason"] == "planning_owner_required" and answer["agent"]["proposal"] is None
    assert current_plan(owner, tid) == original


def test_confirmation_claim_failure_does_not_start_planning(planning_adapter, replacement_fixture_clock, monkeypatch):
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import OperationalError
    (h,), tid, original = planned_trip(1)
    question = ask_confirmation(monkeypatch, h, tid, original)
    commit = Session.commit
    def fail_claim(self):
        if any(isinstance(obj, ChatMessage) and (obj.agent or {}).get("pending_action", {}).get("status") == "CONFIRMED"
               for obj in self.identity_map.values()):
            raise OperationalError("synthetic claim commit failure", {}, Exception("fixture"))
        return commit(self)
    monkeypatch.setattr(Session, "commit", fail_claim)
    async def forbidden(*a, **kw):
        raise AssertionError("Planning must not start before a successful confirmation claim")
    monkeypatch.setattr(planning_actions, "execute", forbidden)
    response = c.post(f"/trips/{tid}/chat", headers=h, json=dict(text="@TravelSync Yes", reply_to=question["id"]))
    assert response.status_code == 503
    assert current_plan(h, tid) == original
    with SessionLocal() as db:
        assert db.get(ChatMessage, question["id"]).agent["pending_action"]["status"] == "PENDING"


def test_service_timeout_is_distinct_from_solver_failure(planning_adapter, replacement_fixture_clock, monkeypatch):
    from fastapi import HTTPException
    (h,), tid, original = planned_trip(1)
    async def timed_out(payload):
        raise HTTPException(503, "agent_request_timeout")
    monkeypatch.setattr(services, "agent_turn", timed_out)
    answer = post(h, tid, "@TravelSync hello")
    assert answer["agent"]["reason"] == "agent_request_timeout"
    assert "timed out" in answer["text"] and "can't answer" not in answer["text"]
    assert current_plan(h, tid) == original
