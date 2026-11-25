import pytest

pytest.importorskip("ortools")
from pydantic import ValidationError

from optimizer_fixtures import request_data
from ourney_travelsync.geography import haversine_km
from ourney_travelsync.optimizer.api import optimize
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.validation import validate_final


def test_draft_prefers_shorter_geographic_order_and_cannot_pass_final_validation():
    data = request_data(2)
    data.update(transport_mode="drive", provisional=True, enforce_cost=False, optimize_travel=False)
    data["days"][0].update(start_point="start", end_point="end")
    coords = dict(start=(35, 129), A=(35, 129.01), B=(35, 129.02), end=(35, 129.03))
    data["edges"] = [
        dict(date=data["days"][0]["date"], origin=a, destination=b, status="ESTIMATED",
             duration_minutes=10, distance_m=round(1000 * haversine_km(coords[a], coords[b])),
             group_fare=0, walk_minutes=0, provider="haversine-draft-v1")
        for a in ("start", "A", "B") for b in ("A", "B", "end") if a != b
    ]
    result = optimize(data)
    assert [visit.poi_id for visit in result.days[0].items] == ["A", "B"]
    assert result.phases[-2].objective == "haversine_distance_m"
    assert result.phases[-1].objective == "waiting_minutes"
    assert result.validation_status == "NOT_RUN"
    assert not validate_final(result.input_snapshot, result.days).passed
    with pytest.raises(ValidationError, match="estimated routes are restricted"):
        PlanRequest.model_validate({**data, "provisional": False})
