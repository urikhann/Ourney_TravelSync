"""Synthetic schedules: real CP-SAT and independent composition validation."""
from collections import Counter
import pytest
from optimizer_fixtures import request_data
from ourney_travelsync.optimizer.contracts import DailyComposition, PlanRequest
from ourney_travelsync.optimizer.eligibility import check_eligibility
from ourney_travelsync.optimizer.solver import solve_prepared
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.planning_categories import visit_role


def fixture(days=1):
    roles = ["cafe"]*4 + ["meal"]*(3 if days == 1 else 4) + ["activity"]*(5 if days == 1 else 6)
    data = request_data(len(roles), days)
    data.update(enforce_cost=False, enforce_opening_hours=False,
                daily_composition=DailyComposition(max_meals=3 if days == 1 else 2).model_dump(mode="json"), search_seconds=5)
    for i,(p,role) in enumerate(zip(data["candidates"],roles)):
        p.update(group_utility=1000, visit_category=role,
                 activity_category=("nature", "history", "art")[i%3] if role == "activity" else "other")
        p["duration_min"]["value"] = 20
    for edge in data["edges"]:
        edge["duration_minutes"] = 5
    for d in data["days"]:
        d["end_min"] = 1200
    for m in data["members"]:
        for windows in m["available"].values():
            windows[0]["end_min"] = 1200
    return data


def solve(data):
    req=PlanRequest.model_validate(data)
    result=solve_prepared(req,check_eligibility(req))
    return req,result


def test_daily_caps_meal_windows_activity_variety_and_interleaving():
    req,result=solve(fixture())
    assert result.status in ("OPTIMAL","FEASIBLE")
    assert validate_final(req,result.days).passed
    p={x.poi_id:x for x in req.candidates}
    visits=result.days[0].items
    roles=[p[v.poi_id].visit_category for v in visits]
    assert roles.count("cafe") == 2
    assert roles.count("meal") == 3
    assert len({p[v.poi_id].activity_category for v in visits if p[v.poi_id].visit_category == "activity"}) >= 2
    assert all(a=="activity" or b=="activity" for a,b in zip(roles,roles[1:]))
    used=[]
    for v in visits:
        if p[v.poi_id].visit_category == "meal":
            used += [w.name for w in req.daily_composition.meal_windows if w.start_min<=v.start_min and v.departure_min<=w.end_min]
    assert sorted(used)==["breakfast","dinner","lunch"]


def test_caps_apply_per_day_and_pois_do_not_repeat_across_days():
    req,result=solve(fixture(2))
    assert result.status in ("OPTIMAL","FEASIBLE")
    assert validate_final(req,result.days).passed
    p={x.poi_id:x for x in req.candidates}
    for day in result.days:
        counts=Counter(p[v.poi_id].visit_category for v in day.items)
        assert counts["cafe"]==2 and counts["meal"]==2
    ids=[v.poi_id for day in result.days for v in day.items]
    assert len(ids)==len(set(ids))


@pytest.mark.parametrize("change,code", [("cafes","CAFE_LIMIT"),("meals","MEAL_COUNT"),
    ("activities","ACTIVITY_COUNT"),("categories","ACTIVITY_CATEGORY_COUNT"),
    ("windows","MEAL_WINDOW"),("adjacency","CONSECUTIVE_FOOD_DRINK")])
def test_e3_independently_rejects_composition_violations(change,code):
    req,result=solve(fixture())
    assert result.days
    raw=req.model_dump(mode="json")
    policy=raw["daily_composition"]
    if change=="cafes":policy["max_cafes"]=0
    elif change=="meals":policy.update(min_meals=1,max_meals=1)
    elif change=="activities":policy["min_activities"]=10
    elif change=="categories":
        for p in raw["candidates"]:p["activity_category"]="nature"
    elif change=="windows":
        for w in policy["meal_windows"]:w["end_min"]=w["start_min"]+1
    elif change=="adjacency":
        for p in raw["candidates"]:p["visit_category"]="cafe"
    checked=validate_final(PlanRequest.model_validate(raw),result.days)
    assert not checked.passed
    assert any(code in e for e in checked.errors)


def test_impossible_variety_and_three_cafe_locks_fail_without_relaxing_policy():
    data=fixture()
    for p in data["candidates"]:p["activity_category"]="nature"
    assert solve(data)[1].status=="INFEASIBLE"
    data=fixture()
    data["locked_items"]=[dict(poi_id=p["poi_id"],date=data["days"][0]["date"]) for p in data["candidates"][:3]]
    assert solve(data)[1].status=="INFEASIBLE"


def test_cafes_are_not_meals_and_activity_features_count_once():
    assert visit_role({"food":1,"cafe":1}) == ("cafe","other")
    assert visit_role({"food":1}) == ("meal","other")
    assert visit_role({"nature":1,"relax":1}) == ("activity","nature")
