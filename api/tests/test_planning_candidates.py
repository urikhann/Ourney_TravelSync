"""Offline full-catalogue retrieval and app planning integration checks."""
from copy import deepcopy
from fastapi.testclient import TestClient
from .conftest import real_rec
from .test_optimizer_integration import ready, generate, audits, c
from app.db import SessionLocal
from app.models import RecommendationRun
from app.features import FEATURES


def request():
    records = []
    for cluster, lat in (("south", 35.1), ("north", 38.1)):
        for i in range(55):
            features = dict.fromkeys(FEATURES, 0)
            features["cafe" if i < 30 else "nature"] = 1
            records.append(dict(place_id=f"{cluster}-{i}", dataset_version="fixture",
                place_name=f"Fixture {cluster} {i}", category_name="fixture", latitude=lat+i*.0001,
                longitude=129.1, duration_min=30, field_status={"duration": "KNOWN"}, **features))
    ratings = dict.fromkeys(FEATURES, 3)
    ratings.update(cafe=5, nature=4)
    def day(date, latitude):
        p = dict(label="Fixture endpoint", latitude=latitude, longitude=129.1)
        return dict(date=date, start_min=540, end_min=660, start_point=p, end_point=p)
    return dict(schema_version="rec-1.0", trip_id="planning-fixture", dataset_version="fixture",
        feature_schema_version="fs-1.0", features=FEATURES,
        members=[dict(user_id="a", ratings=ratings, exclusions=dict(poi_ids=["south-0"],
                     resolved={"north-0": "tag"}))],
        options=dict(method="gfar", individual_method="feature_mean", top_l=1, group_k=1),
        poi_snapshot=records, planning_days=[day("2026-11-13", 35.1), day("2026-11-14", 38.1)])


def test_full_catalogue_precedes_shortlist_and_day_geography():
    req = request()
    client = TestClient(real_rec.app)
    displayed = client.post("/recommend", json=req).json()
    response = client.post("/planning-candidates", json=req)
    assert response.status_code == 200, response.text
    result = response.json()
    selection = result["planning_candidate_selection"]
    assert len(displayed["group"]) == 1
    assert selection["catalogue_count"] == 110
    assert selection["eligible_catalogue_count"] == 108
    assert selection["selected_count"] == 60
    days = selection["day_candidate_ids"]
    assert all(pid.startswith("south-") for pid in days["2026-11-13"])
    assert all(pid.startswith("north-") for pid in days["2026-11-14"])
    assert {"south-0", "north-0"}.isdisjoint(r["poi_id"] for r in result["group"])
    assert any(int(pid.split("-")[1]) >= 30 for pid in days["2026-11-13"])
    assert any(r["poi_id"] not in {x["poi_id"] for x in displayed["group"]} for r in result["group"])
    assert result == client.post("/planning-candidates", json=req).json()
    for row in result["group"]:
        assert row["candidate_dates"] == [d for d, ids in days.items() if row["poi_id"] in ids]
        assert set(row["member_scores"]) == {"a"}


def test_duration_screen_locks_and_unknown_hours_costs():
    req = request()
    req["poi_snapshot"][1]["duration_min"] = 500
    req["planning_locks"] = [dict(poi_id="south-54", date="2026-11-13")]
    client = TestClient(real_rec.app)
    response = client.post("/planning-candidates", json=req)
    assert response.status_code == 200, response.text
    ids = response.json()["planning_candidate_selection"]["day_candidate_ids"]["2026-11-13"]
    assert "south-1" not in ids and "south-54" in ids
    req["planning_locks"][0]["poi_id"] = "south-0"
    assert client.post("/planning-candidates", json=req).status_code == 422


def test_app_uses_catalogue_and_keeps_bypasses_and_recommendations(planning_adapter):
    headers, tid, rec = ready(individual_method="feature_mean")
    with SessionLocal() as db:
        run = db.get(RecommendationRun, rec["run_id"])
        before = deepcopy(run.response)
    def no_hours_prices(req, response):
        for p in response["pois"]:
            p["operations"] = {}
            p["cost_per_person"] = {}
        return response
    planning_adapter.transform = no_hours_prices
    response = generate(headers, tid)
    assert response.status_code == 200, response.text
    audit = audits(tid)[0]
    selection = audit.input["candidate_retrieval"]["planning_candidate_selection"]
    assert selection["catalogue_count"] == 28
    assert len(audit.optimizer_request["candidates"]) > len(rec["group"])
    assert audit.optimizer_request["enforce_cost"] is False
    assert audit.optimizer_request["enforce_opening_hours"] is False
    assert all(p["allowed_dates"] for p in audit.optimizer_request["candidates"])
    assert planning_adapter.calls[0]["day_candidate_ids"] == selection["day_candidate_ids"]
    assert response.json()["latest_optimization"]["candidate_selection"] == selection
    assert all(item["place"] for day in response.json()["plans"][0]["days"] for item in day["items"])
    with SessionLocal() as db:
        assert db.get(RecommendationRun, rec["run_id"]).response == before


def test_displayed_list_size_does_not_limit_planning(planning_adapter):
    headers, tid, rec = ready(individual_method="feature_mean")
    with SessionLocal() as db:
        run = db.get(RecommendationRun, rec["run_id"])
        # A display-only truncated list is not the planner's candidate source.
        run.response = {**run.response, "group": []}
        db.commit()
    result = generate(headers, tid)
    assert result.status_code == 200, result.text
    assert audits(tid)[0].input["candidate_retrieval"]["planning_candidate_selection"]["catalogue_count"] == 28


def test_app_composition_reserves_restaurants_and_validates_published_day(planning_adapter):
    ratings=dict.fromkeys(FEATURES,3)
    ratings["cafe"]=5
    headers,tid,_=ready(individual_method="feature_mean",ratings=ratings)
    response=generate(headers,tid)
    assert response.status_code==200,response.text
    audit=audits(tid)[0]
    req=audit.optimizer_request
    assert req["daily_composition"]["max_cafes"]==2
    assert req["daily_composition"]["min_meals"]==2
    assert req["enforce_cost"] is False and req["enforce_opening_hours"] is False
    p={x["poi_id"]:x for x in req["candidates"]}
    assert sum(x["visit_category"]=="meal" for x in p.values()) >= 2
    visits=response.json()["plans"][0]["days"][0]["items"]
    roles=[v["visit_category"] for v in visits]
    assert roles.count("cafe")<=2 and 2<=roles.count("meal")<=3
    assert len({v["activity_category"] for v in visits if v["visit_category"]=="activity"})>=2
    assert all(a=="activity" or b=="activity" for a,b in zip(roles,roles[1:]))
