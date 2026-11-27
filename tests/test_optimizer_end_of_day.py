from copy import deepcopy

import pytest

from optimizer_fixtures import DAY, fact, request_data
from ourney_travelsync.optimizer import api
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.optimizer.waiting import end_of_day_gaps, gap_retry_request, improves_gap, waiting_gaps


def early_return(visit_minutes=60):
    raw = request_data(3)
    raw["transport_mode"] = "drive"
    raw["gap_policy"] = {"enabled": False}
    raw["locked_items"] = [dict(poi_id="A", date=DAY, start_min=600), dict(poi_id="B", date=DAY, start_min=790)]
    for candidate, minutes in zip(raw["candidates"], (180, 174, visit_minutes)):
        candidate["duration_min"] = fact(minutes)
        candidate["operations"][DAY]["openings"] = fact([dict(start_min=480, end_min=1080)])
    for edge in raw["edges"]:
        edge["duration_minutes"] = 10
    small = deepcopy(raw)
    small["candidates"] = small["candidates"][:2]
    small["edges"] = [e for e in small["edges"] if "C" not in (e["origin"], e["destination"])]
    baseline = api._optimize_once(small)
    assert baseline.validation_status == "PASSED"
    assert baseline.days[0].end_arrival_min == 16 * 60 + 14
    assert waiting_gaps(PlanRequest.model_validate(raw), baseline.days) == []
    return raw, baseline


@pytest.mark.parametrize("minutes,finish", [(60, 1044), (96, 1080)])
def test_adds_a_visit_and_both_drive_legs_before_1800(minutes, finish):
    raw, baseline = early_return(minutes)
    req = PlanRequest.model_validate(raw)
    gap = end_of_day_gaps(req, baseline.days)[0]
    assert (gap["start_min"], gap["end_min"], gap["duration_minutes"]) == (974, 1080, 106)
    retry = gap_retry_request(req, baseline, 1)
    assert retry is not None
    result = api._optimize_once(retry)
    assert result.validation_status == "PASSED"
    assert [v.poi_id for v in result.days[0].items] == ["A", "B", "C"]
    assert result.days[0].end_arrival_min == finish
    assert [(v.poi_id, v.start_min) for v in result.days[0].items[:2]] == [("A", 600), ("B", 790)]
    assert improves_gap(req, baseline, result)
    assert validate_final(req, result.days).passed


@pytest.mark.parametrize("reason", ["one_minute_too_long", "closed_early", "excluded", "availability", "rest"])
def test_retains_plan_when_the_visit_or_return_cannot_fit(reason):
    raw, baseline = early_return(97 if reason == "one_minute_too_long" else 60)
    if reason == "closed_early":
        raw["candidates"][2]["operations"][DAY]["openings"] = fact([dict(start_min=480, end_min=1000)])
    elif reason == "excluded":
        raw["members"][0]["excluded_poi_ids"] = ["C"]
    elif reason == "availability":
        raw["members"][0]["available"][DAY][0]["end_min"] = 1040
    elif reason == "rest":
        raw["days"][0]["breaks"] = [dict(start_min=974, end_min=1080)]
    assert gap_retry_request(PlanRequest.model_validate(raw), baseline, 1) is None


def test_later_return_without_a_new_place_is_not_an_improvement():
    raw, baseline = early_return()
    small = deepcopy(raw)
    small["candidates"] = small["candidates"][:2]
    small["edges"] = [e for e in small["edges"] if "C" not in (e["origin"], e["destination"])]
    for lock in small["locked_items"]:
        lock["start_min"] += 30
    delayed = api._optimize_once(small)
    assert delayed.validation_status == "PASSED"
    assert delayed.days[0].end_arrival_min == 1004
    assert not improves_gap(PlanRequest.model_validate(raw), baseline, delayed)


def test_optional_cafe_can_fill_the_end_of_day():
    raw, baseline = early_return()
    raw["candidates"][2]["visit_category"] = "cafe"
    req = PlanRequest.model_validate(raw)
    result = api._optimize_once(gap_retry_request(req, baseline, 1))
    assert result.validation_status == "PASSED"
    assert result.days[0].items[-1].poi_id == "C"


def test_longer_driving_legs_are_allowed_when_the_whole_visit_fits():
    raw, baseline = early_return(30)
    for edge in raw["edges"]:
        if "C" in (edge["origin"], edge["destination"]):
            edge["duration_minutes"] = 40
    req = PlanRequest.model_validate(raw)
    retry = gap_retry_request(req, baseline, 1)
    assert retry is not None
    result = api._optimize_once(retry)
    assert result.validation_status == "PASSED"
    assert result.days[0].end_arrival_min == 1074
