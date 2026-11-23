"""Authenticated catalogue → GFAR → adapter → solver → E3 breakfast path."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import DatasetVersion, Poi, RecommendationRun
from ourney_travelsync.optimizer.contracts import DailyComposition
from .test_optimizer_integration import ready, generate, audits, configuration

DAY = "2026-11-06"


def fact(value):
    return dict(value=value, status="KNOWN", source="synthetic breakfast API fixture",
                verified_at="2026-11-01T00:00:00Z")


@pytest.mark.parametrize("with_hours", [True, False])
def test_breakfast_evidence_is_pinned_and_unknown_serving_hours_block_planning(planning_adapter, with_hours):
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
                metadata = {"breakfast_offering": fact(True)}
                if with_hours:
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
        body = configuration()
        body["days"][0].update(start_min=420, end_min=840)
        body["daily_composition"] = DailyComposition(breakfast_enabled=True).model_dump(mode="json")
        result = generate(headers, tid, body)
        audit = audits(tid)[0]
        if with_hours:
            assert result.status_code == 200, result.text
            candidate = next(p for p in audit.optimizer_request["candidates"] if p["poi_id"] == pid)
            assert candidate["breakfast_offering"]["source"] == "synthetic breakfast API fixture"
            assert candidate["breakfast_hours"][DAY]["value"] == [dict(start_min=480, end_min=600)]
            visit = next(v for v in audit.response["days"][0]["items"] if v["poi_id"] == pid)
            assert 480 <= visit["start_min"] < visit["departure_min"] <= 600
            assert audit.response["validation_status"] == "PASSED"
        else:
            assert result.status_code == 409, result.text
            assert audit.response["error_code"] == "NO_FEASIBLE_ITINERARY"
            assert not audit.response["days"]
        with SessionLocal() as db:
            assert db.get(RecommendationRun, rec["run_id"]).response == original_rec
    finally:
        with SessionLocal() as db:
            db.get(DatasetVersion, version).status = "ARCHIVED"
            db.get(DatasetVersion, old_version).status = "ACTIVE"
            db.commit()
