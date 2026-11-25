"""Synthetic schedules exercising selected-edge waiting and bounded repairs."""
from copy import deepcopy

import pytest

pytest.importorskip("ortools")

from optimizer_fixtures import DAY, fact, request_data
from ourney_travelsync.optimizer import api
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.optimizer.waiting import gap_retry_request, waiting_gaps
from test_optimizer_meals import fixture


def locked_gap(count=2):
    raw = request_data(count)
    raw["gap_policy"] = {"enabled": False}
    raw["locked_items"] = [dict(poi_id="A", date=DAY, start_min=600),
                           dict(poi_id="B", date=DAY, start_min=900)]
    return raw


def test_avoidable_wait_is_zero_after_prior_objectives():
    raw = request_data(3)
    raw["gap_policy"] = {"enabled": False}
    result = api.optimize(raw)
    assert result.validation_status == "PASSED"
    assert result.metrics["waiting_minutes"] == 0
    assert result.phases[-1].objective == "waiting_minutes"
    assert [p.objective_value for p in result.phases[:3]] == [2700, 6000, 120]
    assert all(v.start_min == v.arrival_min for v in result.days[0].items[1:])


def test_fixed_lunch_and_dinner_preserve_required_wait():
    raw = fixture(count=2)
    raw["gap_policy"] = {"enabled": False}
    raw["daily_composition"]["meal_overrides"] = [
        dict(date=DAY, name="lunch", fixed_start_min=720),
        dict(date=DAY, name="dinner", fixed_start_min=1080)]
    result = api.optimize(raw)
    assert result.validation_status == "PASSED"
    assert [v.start_min for v in result.days[0].items] == [720, 1080]
    assert result.metrics["waiting_minutes"] == 330
    assert result.metrics["waiting_gaps"][0]["reason"] == "fixed_dinner"


def test_rest_is_preserved_and_excluded_from_gap_fill_trigger():
    raw = locked_gap()
    raw["days"][0]["breaks"] = [dict(start_min=690, end_min=900)]
    req = PlanRequest.model_validate(raw)
    result = api.optimize(req)
    assert result.validation_status == "PASSED"
    gap = waiting_gaps(req, result.days)[0]
    assert gap["duration_minutes"] == gap["intentional_break_minutes"] == 210
    assert gap["action"] == "buffer" and gap["reason"] == "rest"
    assert gap_retry_request(req, result, 1) is None
    visit = result.days[0].items[-1].model_copy(update={"start_min": 880, "departure_min": 940})
    bad_day = result.days[0].model_copy(update={"items": (result.days[0].items[0], visit)})
    assert any("BREAK_VISIT_OVERLAP" in e for e in validate_final(req, (bad_day,)).errors)


def test_shared_wait_excludes_initial_delay_and_unused_edges():
    raw = locked_gap()
    result = api.optimize(raw)
    assert result.days[0].items[0].start_min - result.days[0].items[0].arrival_min == 30
    assert result.metrics["waiting_minutes"] == 210
    assert len(result.metrics["waiting_gaps"]) == 1


@pytest.mark.parametrize("minutes,action", [(30,"buffer"),(31,"rest"),(60,"rest"),(61,"fill"),(120,"fill"),(121,"reoptimize")])
def test_configurable_threshold_boundaries(minutes, action):
    raw = locked_gap()
    raw["locked_items"][1]["start_min"] = 690 + minutes
    req = PlanRequest.model_validate(raw)
    result = api.optimize(req)
    assert waiting_gaps(req, result.days)[0]["action"] == action


def test_bounded_retry_inserts_nearby_activity_and_keeps_existing_times(monkeypatch):
    raw = locked_gap(3)
    raw["gap_policy"] = {"enabled": True}
    req = PlanRequest.model_validate(raw)
    # A valid incumbent from a limited first search omitted C. The retry must
    # recover using the same full snapshot through optimize's normal entry point.
    small = deepcopy(raw)
    small["candidates"] = small["candidates"][:2]
    baseline = api._optimize_once(small)
    solve = api._optimize_once
    calls = []

    def first_incumbent(request):
        calls.append(request)
        if len(calls) == 1:
            return solve(request.model_copy(update={"candidates": request.candidates[:2]})).model_copy(update={"input_snapshot": request})
        return solve(request)

    monkeypatch.setattr(api, "_optimize_once", first_incumbent)
    result = api.optimize(req)
    assert len(calls) == 2
    assert result.metrics["gap_fill_applied"]
    assert result.metrics["waiting_minutes"] < baseline.metrics["waiting_minutes"]
    visits = {v.poi_id: v for v in result.days[0].items}
    assert visits["A"].start_min == 600 and visits["B"].start_min == 900
    assert len(visits) == 3
    assert result.input_snapshot == req
    assert result.metrics["allocated_search_seconds"] <= req.search_seconds
    assert validate_final(req, result.days).passed


def test_infeasible_repair_retains_original(monkeypatch):
    raw = locked_gap(3)
    raw["gap_policy"] = {"enabled": True}
    raw["candidates"][2]["operations"][DAY]["openings"] = fact([dict(start_min=950, end_min=1020)])
    result = api.optimize(raw)
    # C may be visited after B, but cannot be forced into the locked gap.
    assert result.validation_status == "PASSED"
    assert not result.metrics["gap_fill_applied"]
    assert result.metrics["waiting_minutes"] == 210
    assert result.metrics["allocated_search_seconds"] <= 2


def test_nearby_filter_uses_both_known_route_legs():
    raw = locked_gap(3)
    baseline = api.optimize(locked_gap())
    for edge in raw["edges"]:
        if edge["origin"] == "C":
            edge["duration_minutes"] = 31
    raw["gap_policy"] = {"rest_minutes": 60, "reoptimize_minutes": 300}
    assert gap_retry_request(PlanRequest.model_validate(raw), baseline, 1) is None
