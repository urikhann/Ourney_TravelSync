"""Authenticated catalogue → GFAR → adapter → solver → E3 breakfast path."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import config
from app.db import SessionLocal
from app.models import DatasetVersion, Poi, RecommendationRun
from ourney_travelsync.optimizer.contracts import DailyComposition
from .test_optimizer_integration import ready, generate, audits, configuration

DAY = "2026-11-06"


def fact(value):
    return dict(value=value, status="KNOWN", source="synthetic breakfast API fixture",
                verified_at="2026-11-01T00:00:00Z")


@pytest.mark.parametrize(("breakfast", "mode"), [("verified", "walk"), ("inferred", "walk"),
                                                    ("omitted", "walk"), ("inferred", "drive"),
                                                    ("route_too_long", "drive")])
def test_optional_breakfast_through_full_planning_flow(planning_adapter, monkeypatch, breakfast, mode):
    monkeypatch.setattr(config, "OPTIMIZER_SEARCH_SECONDS", 20)
    # Build a new version before any runs. Never mutate metadata pinned by a run.
    version = "breakfast-test-" + uuid4().hex[:12]
    with SessionLocal() as db:
        previous = db.scalar(select(DatasetVersion).where(DatasetVersion.status == "ACTIVE"))
        old_version = previous.version
        previous.status = "ARCHIVED"
        db.add(DatasetVersion(version=version, status="DRAFT", feature_schema_version="fs-1.0",
                              mapping_version="synthetic", poi_count=28))
        db.flush()
        places = db.scalars(select(Poi).where(Poi.dataset_version == old_version)).all()
        pid = next(p.id for p in places if p.features.get("cafe"))
        for place in places:
            values = {a.key: deepcopy(getattr(place, a.key)) for a in Poi.__mapper__.column_attrs}
            values["dataset_version"] = version
            clone = Poi(**values)
            if place.id == pid:
                metadata = {"representative_menu": "Brunch"} if breakfast in ("inferred", "route_too_long") else {}
                if breakfast == "verified":
                    metadata["breakfast_offering"] = fact(True)
                    metadata["breakfast_hours"] = {DAY: fact([dict(start_min=480, end_min=600)])}
                clone.enriched_metadata = metadata
            db.add(clone)
        db.flush()
        db.get(DatasetVersion, version).status = "ACTIVE"
        db.commit()
    try:
        headers, tid, rec = ready(individual_method="feature_mean")
        with SessionLocal() as db:
            original_rec = deepcopy(db.get(RecommendationRun, rec["run_id"]).response)
        if mode == "drive":
            def selected_routes(request, response):
                legs = {(leg["date"], leg["origin"], leg["destination"]) for leg in request["route_legs"]}
                response["edges"] = [edge for edge in response["edges"]
                                     if (edge["date"], edge["origin"], edge["destination"]) in legs]
                response["routing_attempts"] = request["routing_attempts"]
                for edge in response["edges"]:
                    blocked = breakfast == "route_too_long" and pid in (edge["origin"], edge["destination"])
                    edge["duration_minutes"] = 300 if blocked else 1
                return response

            planning_adapter.transform = selected_routes
        body = configuration()
        body["transport_mode"] = mode
        body["days"][0].update(start_min=420, end_min=840)
        body["daily_composition"] = DailyComposition(breakfast_enabled=True).model_dump(mode="json")
        result = generate(headers, tid, body)
        audit = audits(tid)[0]
        assert result.status_code == 200, result.text
        assert audit.response["validation_status"] == "PASSED"
        day = result.json()["plans"][0]["days"][0]
        assignments = [v for v in day["items"] if v.get("breakfast_status")]
        omitted = breakfast in ("omitted", "route_too_long")
        assert day["breakfast_omitted"] is omitted
        if omitted:
            assert not assignments
            assert day["items"]
        else:
            assert len(assignments) == 1
            visit = assignments[0]
            assert visit["poi_id"] == pid
            assert visit["breakfast_status"] == breakfast
            assert (480 if breakfast == "verified" else 420) <= visit["start_min"] < visit["departure_min"] <= 600
            candidate = next(p for p in audit.optimizer_request["candidates"] if p["poi_id"] == pid)
            if breakfast == "inferred":
                assert candidate["breakfast_suitable"] and not candidate["breakfast_hours"]
            else:
                assert candidate["breakfast_offering"]["source"] == "synthetic breakfast API fixture"
                assert candidate["breakfast_hours"][DAY]["value"] == [dict(start_min=480, end_min=600)]
        with SessionLocal() as db:
            assert db.get(RecommendationRun, rec["run_id"]).response == original_rec
    finally:
        with SessionLocal() as db:
            db.get(DatasetVersion, version).status = "ARCHIVED"
            db.get(DatasetVersion, old_version).status = "ACTIVE"
            db.commit()
