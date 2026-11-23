from copy import deepcopy

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("ortools")

from optimizer_fixtures import request_data
from ourney_travelsync.optimizer.api import optimize
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.planning_categories import visit_role


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
