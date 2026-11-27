"""Timing survives the authenticated generation, storage and refresh paths."""
from .test_optimizer_integration import c, configuration, generate, ready


def test_waiting_and_breaks_refresh_after_rebuilding(compact_planning_adapter):
    headers, trip_id, _ = ready(individual_method="feature_mean")
    body = configuration()
    day = body["days"][0]["date"]
    body["budget_total"] = 0
    body["duration_overrides"] = {"bmuseum": 30, "xsky": 30}
    body["days"][0]["breaks"] = [dict(start_min=900, end_min=960)]
    body["locked_items"] = [dict(poi_id="bmuseum", date=day, start_min=840),
                            dict(poi_id="xsky", date=day, start_min=990)]
    previous_wait = None
    for start in (990, 1020):
        body["locked_items"][1]["start_min"] = start
        response = generate(headers, trip_id, body)
        assert response.status_code == 200, response.text
        plan = response.json()["plans"][0]
        stored = c.get(f"/trips/{trip_id}/itineraries", headers=headers).json()["plans"][0]
        assert stored["days"] == plan["days"]
        gaps = plan["days"][0]["gaps"]
        assert gaps
        visits = {v["poi_id"]: v for v in plan["days"][0]["items"]}
        for gap in gaps:
            visit = visits[gap["before_poi_id"]]
            assert gap["start_min"] == visit["arrival_min"]
            assert gap["end_min"] == visit["start_min"]
            assert gap["duration_minutes"] == visit["start_min"] - visit["arrival_min"]
        assert plan["stats"]["waiting_minutes"] == sum(g["duration_minutes"] for g in gaps)
        assert not plan["stats"]["budget_enforced"]
        wait = next(g for g in gaps if g["before_poi_id"] == "xsky")
        assert wait["intentional_break_minutes"] == 60
        if previous_wait is not None:
            assert wait["duration_minutes"] == previous_wait + 30
        previous_wait = wait["duration_minutes"]


def test_invalid_break_is_rejected_before_planning(compact_planning_adapter):
    headers, trip_id, _ = ready(individual_method="feature_mean")
    body = configuration()
    body["days"][0]["breaks"] = [dict(start_min=500, end_min=600)]
    assert generate(headers, trip_id, body).status_code == 422
    assert not compact_planning_adapter.calls
