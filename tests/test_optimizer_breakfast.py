"""Synthetic evidence only; no fixture describes a real breakfast venue."""
from copy import deepcopy

import pytest

pytest.importorskip("ortools")
from optimizer_fixtures import DAY, fact
from test_optimizer_meals import fixture
from ourney_travelsync.breakfast_metadata import breakfast_metadata
from ourney_travelsync.optimizer.api import optimize
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.meals import effective_meals
from ourney_travelsync.optimizer.validation import validate_final


def breakfast_request(role="cafe"):
    raw = fixture(420, 600, count=2)
    raw["daily_composition"]["breakfast_enabled"] = True
    raw["allow_unknown_opening_hours"] = True
    p = raw["candidates"][0]
    p.update(visit_category=role, breakfast_offering=fact(True),
             breakfast_hours={DAY: fact([dict(start_min=450, end_min=570)])})
    raw["candidates"][1]["visit_category"] = "activity"
    return raw


@pytest.mark.parametrize("role", ["meal", "cafe"])
def test_breakfast_uses_verified_full_stay_and_replays_without_score_changes(role):
    raw = breakfast_request(role)
    before = deepcopy(raw)
    result = optimize(raw)
    assert result.validation_status == "PASSED", result
    visit = next(v for v in result.days[0].items if v.poi_id == "A")
    assert 450 <= visit.start_min < visit.departure_min <= 570
    assert validate_final(result.input_snapshot, result.days).passed
    replay = optimize(result.input_snapshot)
    assert replay.days == result.days
    assert replay.metrics["snapshot_sha256"] == result.metrics["snapshot_sha256"]
    assert raw == before
    for field in ("group_rank", "group_utility", "personal_scores", "relevance"):
        assert getattr(result.input_snapshot.candidates[0], field) == raw["candidates"][0][field]


@pytest.mark.parametrize("bad", ["offering", "false", "estimated", "source", "stamp", "hours",
    "hours_estimated", "hours_empty", "late", "cutoff", "split", "openings", "closed_unknown", "closed", "travel"])
def test_unknown_or_infeasible_breakfast_is_not_silently_replaced(bad):
    raw = breakfast_request()
    p = raw["candidates"][0]
    if bad == "offering": p.pop("breakfast_offering")
    elif bad == "false": p["breakfast_offering"] = fact(False)
    elif bad == "estimated": p["breakfast_offering"]["status"] = "ESTIMATED"
    elif bad == "source": p["breakfast_offering"].pop("source")
    elif bad == "stamp": p["breakfast_offering"].pop("verified_at")
    elif bad == "hours": p["breakfast_hours"] = {}
    elif bad == "hours_estimated": p["breakfast_hours"][DAY]["status"] = "ESTIMATED"
    elif bad == "hours_empty": p["breakfast_hours"][DAY]["value"] = []
    elif bad == "late": p["breakfast_hours"][DAY]["value"] = [dict(start_min=600, end_min=700)]
    elif bad == "cutoff": p["breakfast_hours"][DAY]["value"] = [dict(start_min=450, end_min=479)]
    elif bad == "split": p["breakfast_hours"][DAY]["value"] = [dict(start_min=450, end_min=465), dict(start_min=480, end_min=495)]
    elif bad == "openings": p["operations"] = {}
    elif bad == "closed_unknown": p["operations"][DAY].pop("closed")
    elif bad == "closed": p["operations"][DAY]["closed"] = fact(True)
    elif bad == "travel":
        for edge in raw["edges"]: edge["duration_minutes"] = 200
    result = optimize(raw)
    assert result.validation_status != "PASSED"
    assert not result.days
    # There is still an activity; breakfast is the unsatisfied requirement.
    assert any("breakfast:REQUIRED_MEAL_UNSCHEDULED" in e for e in result.validation_errors)


@pytest.mark.parametrize("bad", ["offering", "hours", "opening", "closed"])
def test_e3_rejects_tampered_breakfast_evidence(bad):
    raw = breakfast_request()
    result = optimize(raw)
    assert result.validation_status == "PASSED"
    p = raw["candidates"][0]
    if bad == "offering": p["breakfast_offering"] = fact(False)
    elif bad == "hours": p["breakfast_hours"] = {}
    elif bad == "opening": p["operations"][DAY]["openings"] = fact([])
    elif bad == "closed": p["operations"][DAY]["closed"] = fact(True)
    assert not validate_final(PlanRequest.model_validate(raw), result.days).passed


def test_cafe_breakfast_counts_toward_meal_cap():
    raw = breakfast_request()
    raw["daily_composition"]["max_meals"] = 0
    assert optimize(raw).solver_status == "INFEASIBLE"


def test_breakfast_disabled_preserves_ordinary_cafe_and_lunch_dinner_policy():
    raw = breakfast_request()
    raw["daily_composition"]["breakfast_enabled"] = False
    raw["candidates"][0].pop("breakfast_offering")
    result = optimize(raw)
    assert result.validation_status == "PASSED"
    assert [m.name for m in effective_meals(result.input_snapshot.days[0], result.input_snapshot.daily_composition)] == ["lunch", "dinner"]


def test_breakfast_lunch_dinner_use_distinct_visits_and_cafe_cannot_cover_lunch():
    raw = breakfast_request()
    raw["days"][0]["end_min"] = 1260
    for member in raw["members"]: member["available"][DAY][0]["end_min"] = 1260
    assert optimize(raw).solver_status == "INFEASIBLE"
    raw = fixture(420,1260,count=4)
    raw["daily_composition"]["breakfast_enabled"] = True
    raw["candidates"][0].update(breakfast_request()["candidates"][0])
    raw["candidates"][-1]["visit_category"] = "activity"
    result = optimize(raw)
    assert result.validation_status == "PASSED", result
    assert len(result.days[0].items) == 4
    assert validate_final(result.input_snapshot, result.days).passed


def test_verified_category_is_supported_but_broad_categories_and_names_are_not():
    record = {"place_name": "Breakfast brunch cafe", "category_name": "cafe"}
    assert not breakfast_metadata(record)[0].known
    record["enriched_metadata"] = {"breakfast_category": fact("brunch_cafe")}
    assert breakfast_metadata(record)[0].known
    record["enriched_metadata"]["breakfast_category"] = fact("cafe")
    assert not breakfast_metadata(record)[0].known
    record["enriched_metadata"] = {"breakfast_offering": fact("true"), "breakfast_hours": {DAY: fact([dict(start_min=600, end_min=420)])}}
    offering, hours = breakfast_metadata(record)
    assert not offering.known and not hours


@pytest.mark.parametrize("start,end,required", [(420,450,True), (570,600,True), (600,690,False), (390,420,False)])
def test_opt_in_breakfast_is_required_even_below_lunch_dinner_overlap_threshold(start,end,required):
    raw = breakfast_request()
    raw["days"][0].update(start_min=start, end_min=end)
    for member in raw["members"]: member["available"][DAY][0].update(start_min=start,end_min=end)
    req = PlanRequest.model_validate(raw)
    assert effective_meals(req.days[0],req.daily_composition)[0].required is required


def test_breakfast_can_start_at_seven_and_finish_at_ten_but_not_after():
    raw = breakfast_request("meal")
    p = raw["candidates"][0]
    p["duration_min"] = fact(180)
    p["breakfast_hours"][DAY] = fact([dict(start_min=420, end_min=600)])
    raw["candidates"] = raw["candidates"][:1]
    result = optimize(raw)
    assert result.validation_status == "PASSED"
    assert result.days[0].items[0].start_min == 420
    assert result.days[0].items[0].departure_min == 600
    p["duration_min"] = fact(181)
    assert optimize(raw).error_code == "INSUFFICIENT_CANDIDATES"


def test_breakfast_overrides_use_effective_window_without_duplicating_slots():
    raw = breakfast_request("meal")
    raw["daily_composition"]["meal_overrides"] = [dict(date=DAY,name="breakfast",fixed_start_min=500)]
    result = optimize(raw)
    assert result.validation_status == "PASSED"
    assert next(v for v in result.days[0].items if v.poi_id == "A").start_min == 500
    policy = result.input_snapshot.daily_composition
    assert [m.name for m in effective_meals(result.input_snapshot.days[0],policy)] == ["breakfast","lunch","dinner"]
