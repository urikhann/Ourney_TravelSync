from copy import deepcopy

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("ortools")

from optimizer_fixtures import request_data
from ourney_travelsync.optimizer.api import optimize
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.planning_categories import visit_role
from ourney_travelsync.optimizer.contracts import DailyComposition


def evening_request():
    data = request_data(1)
    data["candidates"][0]["visit_category"] = "evening"
    data["days"][0]["end_min"] = 1440
    for m in data["members"]:
        next(iter(m["available"].values()))[0]["end_min"] = 1440
    data["enforce_opening_hours"] = False
    data["locked_items"] = [dict(poi_id="A", date=data["days"][0]["date"])]
    return data


def test_bars_are_evening_visits_not_dinner_or_cafes():
    assert visit_role(dict(food=1,cafe=1,category_name="음식점 > 술집 > 호프")) == ("evening","other")
    assert visit_role(dict(food=1,category_name="Pub")) == ("evening","other")
    assert visit_role(dict(food=1,category_name="Barbecue restaurant")) == ("meal","other")
    assert visit_role(dict(food=1,tags=["alcohol"])) == ("evening","other")


def test_evening_is_opt_in_after_1800_and_e3_checks_independently():
    data=evening_request()
    result=optimize(data)
    assert result.validation_status=="PASSED"
    assert result.days[0].items[0].start_min>=1080
    changed=deepcopy(data)
    changed["locked_items"]=[]
    assert not validate_final(PlanRequest.model_validate(changed),result.days).passed
    assert not optimize(changed).days
    changed=deepcopy(data)
    changed["days"][0]["end_min"]=1080
    assert not optimize(changed).days


def test_evening_still_respects_verified_closure_and_member_exclusion():
    data=evening_request()
    data["members"][0]["excluded_poi_ids"]=["A"]
    assert not optimize(data).days
    data=evening_request()
    data["enforce_opening_hours"]=True
    for op in data["candidates"][0]["operations"].values(): op["closed"]["value"]=True
    assert not optimize(data).days


def automatic_evening_request():
    data = request_data(3)
    data['days'][0]['end_min'] = 1440
    for member in data['members']:
        next(iter(member['available'].values()))[0]['end_min'] = 1440
    for i, candidate in enumerate(data['candidates']):
        candidate['visit_category'] = 'evening' if i < 2 else 'activity'
        candidate['tags'] = ['alcohol'] if i < 2 else []
        for op in candidate['operations'].values():
            op['openings']['value'] = [dict(start_min=540, end_min=1440)]
    data['daily_composition'] = DailyComposition(evening_enabled=True, min_activities=1,
        min_activity_categories=1, meal_windows=(), separate_food_drink=False).model_dump(mode='json')
    return data


def test_optional_evening_automatically_selects_one_and_e3_enforces_opt_in():
    data = automatic_evening_request()
    result = optimize(data)
    assert result.validation_status == 'PASSED'
    visits = result.days[0].items
    bars = [v for v in visits if v.poi_id in ('A', 'B')]
    assert len(bars) == 1 and bars[0].start_min >= 1080
    assert any(v.poi_id == 'C' for v in visits)
    data['daily_composition']['evening_enabled'] = False
    assert not validate_final(PlanRequest.model_validate(data), result.days).passed
    assert {v.poi_id for d in optimize(data).days for v in d.items} == {'C'}


@pytest.mark.parametrize('blocked', ['closure', 'exclusion', 'no_evening_time', 'route_too_long', 'unknown_hours'])
def test_optional_evening_skips_infeasible_choices_without_failing_day(blocked):
    data = automatic_evening_request()
    if blocked == 'no_evening_time':
        data['days'][0]['end_min'] = 1080
    for candidate in data['candidates'][:2]:
        if blocked == 'closure':
            for op in candidate['operations'].values():
                op['closed']['value'] = True
        if blocked == 'unknown_hours':
            candidate['operations'] = {}
    if blocked == 'exclusion':
        data['members'][0]['excluded_tags'] = ['alcohol']
    if blocked == 'route_too_long':
        for edge in data['edges']:
            if edge['origin'] in ('A', 'B') or edge['destination'] in ('A', 'B'):
                edge['duration_minutes'] = 600
    result = optimize(data)
    assert result.validation_status == 'PASSED'
    assert {v.poi_id for d in result.days for v in d.items} == {'C'}


def test_evening_cannot_satisfy_required_activity_count():
    data = automatic_evening_request()
    data['daily_composition'].update(min_activities=2, min_activity_categories=1)
    assert not optimize(data).days


def test_explicit_evening_locks_survive_automatic_preference():
    data = automatic_evening_request()
    data['locked_items'] = [dict(poi_id=pid, date=data['days'][0]['date']) for pid in ('A', 'B')]
    result = optimize(data)
    assert result.validation_status == 'PASSED'
    assert {v.poi_id for d in result.days for v in d.items} == {'A', 'B', 'C'}
    data['locked_items'] = []
    checked = validate_final(PlanRequest.model_validate(data), result.days)
    assert not checked.passed and any('EVENING_LIMIT' in error for error in checked.errors)
