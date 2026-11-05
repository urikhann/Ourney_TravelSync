from copy import deepcopy

import pytest
pytest.importorskip("ortools")

from optimizer_fixtures import DAY, request_data
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.eligibility import check_eligibility
from ourney_travelsync.optimizer.solver import solve_prepared
from ourney_travelsync.optimizer.validation import validate_final


def solved(count=2, days=1):
    req = PlanRequest.model_validate(request_data(count, days))
    return req, solve_prepared(req, check_eligibility(req)).days


def test_e3_reconstructs_costs_and_metrics():
    req, days = solved(3, 3)
    result = validate_final(req, days)
    assert result.passed
    assert result.metrics["total_cost"] == 660
    assert result.metrics["visit_count"] == 3
    assert result.metrics["group_utility"] == 2700
    assert result.metrics["minimum_member_utility"] == pytest.approx(0.6)


@pytest.mark.parametrize("field", ["arrival_min", "start_min", "departure_min", "travel_minutes", "stay_minutes", "visit_cost"])
def test_e3_rejects_tampered_visit_fields(field):
    req, days = solved()
    raw = [d.model_dump(mode="json") for d in days]
    raw[0]["items"][0][field] += 1
    assert not validate_final(req, raw).passed


@pytest.mark.parametrize("field", ["end_arrival_min", "return_travel_minutes", "total_cost", "walk_minutes", "travel_minutes", "start_min"])
def test_e3_rejects_tampered_day_fields(field):
    req, days = solved()
    raw = [d.model_dump(mode="json") for d in days]
    raw[0][field] += 1
    assert not validate_final(req, raw).passed


@pytest.mark.parametrize("change", ["budget", "hours", "closure", "access", "exclusion", "member", "fare", "route", "walk", "leg", "cost", "duration", "festival", "lock"])
def test_e3_independently_rechecks_snapshot_constraints(change):
    req, days = solved(1)
    raw = deepcopy(req.model_dump(mode="json"))
    p = raw["candidates"][0]
    if change == "budget":
        raw["budget_total"] = 219
    elif change == "hours":
        p["operations"][DAY]["openings"]["value"][0]["end_min"] = 650
    elif change == "closure":
        p["operations"][DAY]["closed"]["value"] = True
    elif change == "access":
        raw["members"][0]["required_access"] = ["wheelchair"]
    elif change == "exclusion":
        raw["members"][1]["excluded_poi_ids"] = ["A"]
    elif change == "member":
        raw["members"][1]["available"][DAY][0]["end_min"] = 680
    elif change == "fare":
        raw["edges"][0]["group_fare"] = None
    elif change == "route":
        raw["edges"] = []
    elif change == "walk":
        raw["max_total_walk_minutes"] = 9
    elif change == "leg":
        raw["max_leg_minutes"] = 29
    elif change == "cost":
        p["cost_per_person"]["status"] = "UNKNOWN"
    elif change == "duration":
        p["duration_min"]["status"] = "UNKNOWN"
    elif change == "festival":
        p["is_festival"] = True
    elif change == "lock":
        raw["locked_items"] = [dict(poi_id="A", date=DAY, start_min=601)]
    result = validate_final(PlanRequest.model_validate(raw), days)
    assert not result.passed
    assert not result.metrics


def test_e3_rejects_duplicates_missing_dates_and_unknown_pois():
    req, days = solved(3, 3)
    assert not validate_final(req, days[:2]).passed
    raw = [d.model_dump(mode="json") for d in days]
    raw[1]["items"] = deepcopy(raw[0]["items"])
    assert any("DUPLICATE_POI" in e for e in validate_final(req, raw).errors)
    raw[0]["items"][0]["poi_id"] = "invented"
    assert any("UNKNOWN_POI" in e for e in validate_final(req, raw).errors)


def test_e3_revalidates_unchecked_model_copy():
    req, days = solved()
    forged = days[0].model_copy(update={"total_cost": -1})
    assert validate_final(req, (forged,)).errors == ("INVALID_VALIDATION_INPUT",)
