from optimizer_fixtures import prepared, request_data
from ourney_travelsync.optimizer.solver import solve_prepared
import pytest


def test_single_day_waits_and_returns():
    result = solve_prepared(*prepared(request_data(1)))
    assert result.status == "OPTIMAL"
    day = result.days[0]
    assert [v.poi_id for v in day.items] == ["A"]
    assert day.items[0].arrival_min == 570
    assert day.items[0].start_min >= 600
    assert day.items[0].departure_min >= 660
    assert day.end_arrival_min == day.items[-1].departure_min + 30
    assert day.return_travel_minutes == 30
    assert result.phases[0].objective_value == 1000


def test_optional_visits_under_time_limit():
    data = request_data(3)
    data["days"][0]["end_min"] = 700
    result = solve_prepared(*prepared(data))
    assert result.status == "OPTIMAL"
    assert [v.poi_id for v in result.days[0].items] == ["A"]


def test_missing_return_leg_is_infeasible():
    data = request_data(1)
    data["edges"] = [e for e in data["edges"] if e["destination"] != "hotel"]
    result = solve_prepared(*prepared(data))
    assert result.status == "INFEASIBLE"
    assert not result.days


@pytest.mark.parametrize("days", [1, 2, 3])
def test_multiday_no_duplicates_and_no_deleted_dates(days):
    result = solve_prepared(*prepared(request_data(4, days)))
    assert result.status == "OPTIMAL"
    assert len(result.days) == days
    ids = [v.poi_id for d in result.days for v in d.items]
    assert len(ids) == len(set(ids)) == 4
    assert all(d.items for d in result.days)


def test_too_few_pois_cannot_silently_delete_day():
    result = solve_prepared(*prepared(request_data(1, 2)))
    assert result.status == "INFEASIBLE"
    assert not result.days
