import pytest

pytest.importorskip("pydantic")
pytest.importorskip("ortools")

from optimizer_fixtures import prepared, request_data
from ourney_travelsync.optimizer.solver import solve_prepared
from ourney_travelsync.optimizer.comfort import day_burden


@pytest.mark.parametrize("preference", ["indoor", "less_walking"])
def test_day_preference_beats_utility_and_keeps_locks(preference):
    data = request_data(5)
    data["day_preference"] = preference
    data["gap_policy"] = {"enabled": False}
    for p in data["candidates"]:
        p.update(indoor=p["poi_id"] in ("D", "E"), walking_level=0 if p["poi_id"] in ("D", "E") else 1)
    # A is protected; both B and C can be dropped across the day.
    data["locked_items"] = [dict(poi_id="A", date=data["days"][0]["date"], start_min=600)]
    result = solve_prepared(*prepared(data))
    assert result.status == "OPTIMAL"
    ids = {v.poi_id for v in result.days[0].items}
    assert "A" in ids and not {"B", "C"}.intersection(ids)
    if preference == "indoor":
        assert {"D", "E"} <= ids
    assert result.phases[0].objective == ("outdoor_minutes" if preference == "indoor" else "walking_burden")
    req, _ = prepared(data)
    assert day_burden(req, result.days[0], preference) == result.phases[0].objective_value


def test_unknown_indoor_status_does_not_count_as_shelter():
    data = request_data(2)
    data["day_preference"] = "indoor"
    data["candidates"][1]["indoor"] = True
    result = solve_prepared(*prepared(data))
    assert [v.poi_id for v in result.days[0].items] == ["B"]


def test_walking_preference_counts_route_legs_including_return():
    data = request_data(2)
    data["day_preference"] = "less_walking"
    for p in data["candidates"]:
        p["walking_level"] = 0
    for edge in data["edges"]:
        edge["walk_minutes"] = 20 if edge["origin"] == "A" else 1
    result = solve_prepared(*prepared(data))
    # A has higher relevance utility, but returning from A entails more walking.
    assert [v.poi_id for v in result.days[0].items] == ["B"]
    assert result.days[0].walk_minutes == 2
    assert result.phases[0].objective_value == 200
