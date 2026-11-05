from copy import deepcopy

import pytest

from optimizer_fixtures import DAY, fact, request_data
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.eligibility import check_eligibility
from ourney_travelsync.optimizer.solver import solve_prepared


def run(data):
    req = PlanRequest.model_validate(data)
    eligibility = check_eligibility(req)
    return solve_prepared(req, eligibility), eligibility


@pytest.mark.parametrize("field", ["duration_min", "cost_per_person"])
def test_missing_required_field_excludes_without_mutating_recommendation(field):
    data = request_data(1)
    data["candidates"][0][field] = {}
    original = deepcopy(data)
    res, es = run(data)
    assert not es[0].schedule_eligible
    assert any("UNKNOWN" in r for r in es[0].reasons)
    assert res.status == "INFEASIBLE"
    assert data == original


@pytest.mark.parametrize("what", ["openings", "closed"])
def test_unknown_operating_data_never_becomes_all_day_open(what):
    data = request_data(1)
    data["candidates"][0]["operations"][DAY][what] = {}
    res, es = run(data)
    assert res.status == "INFEASIBLE"
    assert any("OPERATING_INFO_UNKNOWN" in r for r in es[0].reasons)


def test_estimates_require_explicit_provenance_and_cost_upper_bound():
    data = request_data(1)
    p = data["candidates"][0]
    p["duration_min"].update(status="ESTIMATED", rule_version="fixture-rule1")
    p["cost_per_person"].update(status="ESTIMATED")
    assert not run(data)[1][0].schedule_eligible
    p["cost_per_person"]["user_upper_bound"] = True
    assert run(data)[1][0].schedule_eligible


@pytest.mark.parametrize("budget,status", [(219, "INFEASIBLE"), (220, "OPTIMAL")])
def test_group_budget_includes_both_fares(budget, status):
    data = request_data(1)
    data["budget_total"] = budget
    res, _ = run(data)
    assert res.status == status
    if res.days:
        assert res.days[0].total_cost == 220


@pytest.mark.parametrize("change", ["leg", "walk", "exclusion", "tag", "access", "event", "closed", "return"])
def test_hard_constraint_conflicts(change):
    data = request_data(1)
    p = data["candidates"][0]
    if change == "leg":
        data["max_leg_minutes"] = 29
    elif change == "walk":
        data["max_total_walk_minutes"] = 9
    elif change == "exclusion":
        data["members"][1]["excluded_poi_ids"] = ["A"]
    elif change == "tag":
        data["members"][1]["excluded_tags"] = ["hiking"]
        p["tags"] = ["hiking"]
    elif change == "access":
        data["members"][0]["required_access"] = ["wheelchair"]
    elif change == "event":
        p["is_festival"] = True
    elif change == "closed":
        p["operations"][DAY]["closed"] = fact(True)
    elif change == "return":
        data["days"][0]["end_min"] = 680
        data["edges"][1]["duration_minutes"] = 40
    assert run(data)[0].status == "INFEASIBLE"


def test_known_access_and_festival_validity():
    data = request_data(1)
    p = data["candidates"][0]
    data["members"][0]["required_access"] = ["wheelchair"]
    p["access"] = {"wheelchair": fact(True)}
    p["is_festival"] = True
    p["operations"][DAY]["event_active"] = fact(True)
    assert run(data)[0].status == "OPTIMAL"


def test_breaks_are_not_merged_into_one_open_interval():
    data = request_data(1)
    data["candidates"][0]["operations"][DAY]["openings"] = fact([
        dict(start_min=600, end_min=630), dict(start_min=660, end_min=690)])
    assert not run(data)[1][0].schedule_eligible


def test_member_availability_is_a_hard_bound():
    data = request_data(1)
    data["members"][1]["available"][DAY] = [dict(start_min=540, end_min=680)]
    assert run(data)[0].status == "INFEASIBLE"  # return arrives at 690


def test_unavailable_fixed_departure_is_conflict():
    data = request_data(1)
    data["members"][1]["available"][DAY] = [dict(start_min=600, end_min=1080)]
    assert run(data)[0].status == "INFEASIBLE"


def test_lock_is_required_at_exact_time():
    data = request_data(2)
    data["locked_items"] = [dict(poi_id="B", date=DAY, start_min=600)]
    res, _ = run(data)
    assert next(v for v in res.days[0].items if v.poi_id == "B").start_min == 600
    data["locked_items"][0]["start_min"] = 590
    assert run(data)[0].status == "INFEASIBLE"


def test_unknown_route_fare_is_not_zero():
    data = request_data(1)
    data["edges"][1]["group_fare"] = None
    res, es = run(data)
    assert res.status == "INFEASIBLE"
    assert any("ROUTE_UNAVAILABLE" in r for r in es[0].reasons)
