"""Deterministic synthetic meal policy, scheduling and independent E3 checks."""
from copy import deepcopy
from datetime import date

import pytest

pytest.importorskip("ortools")
pytest.importorskip("pydantic")

from pydantic import ValidationError

from optimizer_fixtures import DAY, fact, request_data
from ourney_travelsync.optimizer.api import optimize
from ourney_travelsync.optimizer.contracts import DailyComposition, MealOverride, PlanRequest
from ourney_travelsync.optimizer.meals import effective_meals, update_meal_constraints
from ourney_travelsync.optimizer.validation import validate_final


def fixture(start=540, end=1260, count=3, days=1, duration=30):
    raw = request_data(count, days)
    raw["daily_composition"] = DailyComposition(
        min_activities=0, min_activity_categories=0, separate_food_drink=False).model_dump(mode="json")
    for day in raw["days"]:
        day.update(start_min=start, end_min=end)
    for member in raw["members"]:
        for windows in member["available"].values():
            windows[0].update(start_min=start, end_min=end)
    for p in raw["candidates"]:
        p.update(visit_category="meal")
        p["duration_min"] = fact(duration)
        for op in p["operations"].values():
            op["openings"] = fact([dict(start_min=0, end_min=1440)])
    for e in raw["edges"]:
        e["duration_minutes"] = 0
    return raw


@pytest.mark.parametrize("start,end,expected", [
    (540,1260,{"lunch","dinner"}), (540,840,{"lunch"}),
    (840,1260,{"dinner"}), (870,1050,set()), (690,735,{"lunch"}),
    (690,734,set()), (1080,1320,{"dinner"}),
])
def test_requirements_use_actual_day_overlap(start, end, expected):
    req = PlanRequest.model_validate(fixture(start,end))
    assert {m.name for m in effective_meals(req.days[0],req.daily_composition) if m.required} == expected


@pytest.mark.parametrize("start,end,expected", [
    (540,1260,2), (540,840,1), (840,1260,1), (870,1050,0),
    (690,735,1), (690,734,0),
])
def test_end_to_end_defaults_and_boundaries(start,end,expected):
    raw = fixture(start,end)
    # An activity provides a valid visit on days with no meal window overlap.
    raw["candidates"][-1]["visit_category"] = "activity"
    result = optimize(raw)
    assert result.validation_status == "PASSED", result
    roles = {p["poi_id"]:p["visit_category"] for p in raw["candidates"]}
    # Optional restaurant visits are allowed when they improve group utility.
    effective=effective_meals(result.input_snapshot.days[0],result.input_snapshot.daily_composition)
    assert sum(m.required for m in effective)==expected
    assert sum(roles[v.poi_id]=="meal" for v in result.days[0].items) >= expected


def test_multiple_days_have_independent_requirements():
    raw = fixture(count=4,days=2)
    raw["days"][0]["end_min"] = 840
    raw["days"][1]["start_min"] = 870
    result = optimize(raw)
    assert result.validation_status == "PASSED"
    for day in result.days:
        assert len(day.items)==1
    assert result.days[0].items[0].departure_min <= 870
    assert result.days[1].items[0].start_min >= 1050


@pytest.mark.parametrize("conflict", ["duration", "closing", "daily_end", "travel", "cafe", "activity", "insufficient", "budget", "exclusion", "endpoint"])
def test_required_meals_are_never_silently_dropped(conflict):
    raw = fixture(count=2)
    if conflict == "duration":
        raw=fixture(690,735,duration=46)
    elif conflict == "closing":
        for p in raw["candidates"]:
            p["operations"][DAY]["openings"] = fact([dict(start_min=690,end_min=710)])
    elif conflict == "daily_end":
        raw=fixture(690,735,duration=60)
    elif conflict == "travel":
        for e in raw["edges"]: e["duration_minutes"]=300
    elif conflict in ("cafe","activity"):
        for p in raw["candidates"]: p["visit_category"]=conflict
    elif conflict == "insufficient": raw["candidates"]=raw["candidates"][:1]
    elif conflict == "budget": raw["budget_total"]=0
    elif conflict == "exclusion": raw["members"][0]["excluded_poi_ids"]=["A","B"]
    elif conflict == "endpoint":
        for e in raw["edges"]:
            if e["destination"]=="hotel": e["duration_minutes"]=1440
    result=optimize(raw)
    assert result.validation_status != "PASSED"
    assert result.error_code in ("NO_FEASIBLE_ITINERARY","INSUFFICIENT_CANDIDATES")
    assert not result.days


def changed(raw, name="lunch", **kwargs):
    return update_meal_constraints(PlanRequest.model_validate(raw),
        MealOverride(date=date.fromisoformat(DAY),name=name,**kwargs),constraint_version="meal-change-1")


@pytest.mark.parametrize("name,time", [("lunch",810),("lunch",900),("dinner",1020),("dinner",1230)])
def test_fixed_overrides_reoptimize_inside_and_outside_defaults(name,time):
    req=changed(fixture(end=1380),name,fixed_start_min=time)
    result=optimize(req)
    assert result.validation_status=="PASSED", result
    assert any(v.start_min==time for v in result.days[0].items)
    assert result.input_snapshot==req


@pytest.mark.parametrize("time", [810,900])
def test_preferred_time_breaks_ties_without_changing_original_objectives(time):
    req=changed(fixture(),preferred_start_min=time)
    result=optimize(req)
    assert result.validation_status=="PASSED"
    assert any(v.start_min==time for v in result.days[0].items)
    assert [p.objective for p in result.phases]==["group_utility","minimum_relevance","travel_minutes","meal_preference"]
    assert result.phases[-1].objective_value==0


def test_override_changes_only_its_day_and_meal_and_preserves_original():
    raw=fixture(count=4,days=2)
    before=deepcopy(raw)
    req=changed(raw,fixed_start_min=900)
    assert raw==before
    first=effective_meals(req.days[0],req.daily_composition)
    second=effective_meals(req.days[1],req.daily_composition)
    assert first[0].start_min==900 and second[0].start_min==690
    assert first[1]==second[1]


@pytest.mark.parametrize("kwargs", [
    dict(fixed_start_min=500), dict(fixed_start_min=1260),
    dict(fixed_start_min=810,preferred_start_min=800),
    dict(fixed_start_min=900,window=dict(start_min=690,end_min=870)),
    dict(required=True,window=dict(start_min=0,end_min=100)),
])
def test_invalid_overrides_have_meaningful_errors(kwargs):
    with pytest.raises(ValueError,match="meal|preferred|fixed"):
        changed(fixture(),**kwargs)


def test_explicit_meal_survives_below_threshold_and_schedule_can_fail():
    req=changed(fixture(690,734),fixed_start_min=700)
    assert effective_meals(req.days[0],req.daily_composition)[0].required
    result=optimize(req)
    assert result.validation_status=="PASSED"
    raw=req.model_dump(mode="json")
    for p in raw["candidates"]: p["duration_min"]["value"]=40
    assert optimize(raw).error_code=="NO_FEASIBLE_ITINERARY"


def test_changed_window_recalculates_requirement():
    req=changed(fixture(870,1050),window=dict(start_min=900,end_min=960))
    assert effective_meals(req.days[0],req.daily_composition)[0].required
    assert optimize(req).validation_status=="PASSED"


def test_one_visit_cannot_cover_overlapping_overridden_meals():
    raw=fixture(690,840,count=1)
    req=changed(raw,"dinner",window=dict(start_min=690,end_min=870),required=True)
    assert optimize(req).solver_status=="INFEASIBLE"
    original=PlanRequest.model_validate(raw)
    result=optimize(original)
    assert result.validation_status=="PASSED"
    assert not validate_final(req,result.days).passed


@pytest.mark.parametrize("tamper", ["missing", "fixed", "cafe", "window", "duration"])
def test_e3_rejects_forged_meals_without_solver_or_eligibility(tamper):
    raw=fixture()
    result=optimize(raw)
    assert result.validation_status=="PASSED"
    if tamper=="missing":
        for p in raw["candidates"]: p["visit_category"]="activity"
    elif tamper=="fixed":
        existing=result.days[0].items[0].start_min
        raw=changed(raw,fixed_start_min=existing+1).model_dump(mode="json")
    elif tamper=="cafe":
        for p in raw["candidates"]: p["visit_category"]="cafe"
    elif tamper=="window":
        raw["daily_composition"]["meal_overrides"]=[dict(date=DAY,name="lunch",window=dict(start_min=700,end_min=720),required=True)]
    elif tamper=="duration":
        for p in raw["candidates"]: p["duration_min"]["value"]=200
    checked=validate_final(PlanRequest.model_validate(raw),result.days)
    assert not checked.passed


def test_unknown_date_duplicates_and_versions_rejected():
    req=PlanRequest.model_validate(fixture())
    with pytest.raises(ValueError,match="new constraint_version"):
        update_meal_constraints(req,MealOverride(date=date.fromisoformat(DAY),name="lunch"),constraint_version=req.constraint_version)
    with pytest.raises(ValueError,match="unknown date"):
        PlanRequest.model_validate({
            **req.model_dump(mode="json"), "daily_composition": {
                **req.daily_composition.model_dump(mode="json"), "meal_overrides": [dict(date="2030-01-01",name="lunch")]}})
    with pytest.raises(ValidationError,match="duplicate"):
        DailyComposition(meal_overrides=(MealOverride(date=date.fromisoformat(DAY),name="lunch"),)*2)


def test_omitted_policy_enables_dynamic_meals_and_snapshots_defaults():
    raw=fixture()
    del raw["daily_composition"]
    req=PlanRequest.model_validate(raw)
    assert req.daily_composition is not None
    assert [w.name for w in req.daily_composition.meal_windows]==["lunch","dinner"]
    assert req.daily_composition.meal_overlap_min==45


def test_fixed_meal_must_finish_before_restaurant_closes():
    raw=fixture(540,840)
    for p in raw["candidates"]:
        p["operations"][DAY]["openings"]=fact([dict(start_min=690,end_min=750)])
    req=changed(raw,fixed_start_min=740)
    result=optimize(req)
    assert result.error_code=="NO_FEASIBLE_ITINERARY"
    # The same fixture can be scheduled when fixed start leaves a full stay.
    assert optimize(changed(raw,fixed_start_min=720)).validation_status=="PASSED"


def test_legacy_fixed_minimum_does_not_force_extra_meals_on_short_day():
    raw=fixture(540,840)
    raw["daily_composition"]["min_meals"]=2
    result=optimize(raw)
    assert result.validation_status=="PASSED"
    assert len(result.days[0].items)==1
