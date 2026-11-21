"""Disposable SQLite checks; no shared PostgreSQL or external provider is accessed."""
import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest
pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.db import Base
from app.models import DatasetVersion, Poi, RecommendationRun, Trip, User
from app.deployment import initialize, check_startup, expected
from ourney_travelsync import FEATURE_NAMES, load_places
from ourney_travelsync.deployment import configured
from ourney_travelsync.recommenders.hybrid import catalogue_identity

V = "busan-enriched-v4-20261121"

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.fixture(scope="module")
def database(tmp_path_factory):
    engine = create_engine("sqlite:///" + str(tmp_path_factory.mktemp("deployment") / "isolated.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(DatasetVersion(version="historical",status="ACTIVE",feature_schema_version="fs-1.0",mapping_version="test",poi_count=0))
        db.add(User(id="u",email="deployment@example.test",name="test",password_hash="unused"))
        db.flush()
        db.add(Trip(id="t",name="test",city="busan",created_by="u",start_date=date(2026,11,21),end_date=date(2026,11,21)))
        db.flush()
        db.add(RecommendationRun(trip_id="t",dataset_version="historical",feature_schema_version="fs-1.0",model_version="old",method="gfar",member_snapshot={"unchanged":True},request={"dataset_version":"historical"},response={"historical":True}))
        db.commit()
        with pytest.raises(ValueError,match="active dataset mismatch"):
            initialize(db,activate=True)
        db.rollback()
        initialize(db,activate=True,expected_active="historical")
    yield engine
    engine.dispose()

def test_fresh_upgrade_restart_idempotency_and_history(database):
    with Session(database) as db:
        before = dict(db.execute(text("SELECT * FROM recommendation_runs")).mappings().one())
        result = initialize(db,activate=True,expected_active=V)
        assert result["normalized_schedules"] == 74
        assert check_startup(db) == 0
        assert db.get(DatasetVersion,"historical").status == "ARCHIVED"
        assert dict(db.execute(text("SELECT * FROM recommendation_runs")).mappings().one()) == before
        assert db.execute(text("SELECT count(*) FROM poi_enrichment_evidence")).scalar_one() == 1388
        assert db.execute(text("SELECT count(*) FROM poi_enrichment_reviews")).scalar_one() == 3496
        # A restart cannot reactivate the configured release after an explicit switch.
        db.execute(text("UPDATE dataset_versions SET status='ARCHIVED' WHERE version=:v"), {"v":V})
        db.execute(text("UPDATE dataset_versions SET status='ACTIVE' WHERE version='historical'"))
        with pytest.raises(ValueError,match="differs from deployment"):
            check_startup(db)
        assert db.execute(text("SELECT version FROM dataset_versions WHERE status='ACTIVE'")).scalar_one() == "historical"
        db.rollback()


def test_multiple_active_versions_are_rejected(database):
    with Session(database) as db:
        db.execute(text("UPDATE dataset_versions SET status='ACTIVE' WHERE version='historical'"))
        with pytest.raises(ValueError,match="multiple active"):
            check_startup(db)
        with pytest.raises(ValueError,match="multiple active"):
            initialize(db,expected_active=V)
        db.rollback()

@pytest.mark.parametrize("mutation", [
    "UPDATE pois SET name='changed' WHERE dataset_version=:v",
    "UPDATE poi_metadata SET metadata='{}' WHERE dataset_version=:v",
    "DELETE FROM poi_enrichment_evidence WHERE dataset_version=:v",
    "DELETE FROM poi_enrichment_reviews WHERE dataset_version=:v",
    "UPDATE poi_normalized_hours SET schedule='{}' WHERE dataset_version=:v",
    "UPDATE poi_hours_import_batches SET payload_sha256='changed' WHERE dataset_version=:v",
    "DELETE FROM poi_versioned_legacy_hours WHERE dataset_version=:v",
])
def test_existing_version_content_mismatch_is_rejected(database, mutation):
    with Session(database) as db:
        db.execute(text(mutation), {"v":V})
        with pytest.raises(ValueError,match="mismatch"):
            check_startup(db)
        with pytest.raises(ValueError,match="mismatch"):
            initialize(db,expected_active=V)
        db.rollback()

def test_csv_database_cache_identity(database):
    from app.features import FEATURES
    with Session(database) as db:
        pois = list(db.scalars(select(Poi).where(Poi.dataset_version==V)).unique())
        from ourney_travelsync.places import PlaceDataset
        rows = tuple(dict(place_id=p.id,dataset_version=V,place_name=p.name,category_name=p.category,
                          latitude=p.lat,longitude=p.lng,enriched_metadata=p.enriched_metadata,**p.features) for p in pois)
        joined = PlaceDataset(rows,tuple(r["place_id"] for r in rows),np.array([[r[f] for f in FEATURES] for r in rows],float),np.array([[r["latitude"],r["longitude"]] for r in rows]))
        assert catalogue_identity(joined)[2] == catalogue_identity(load_places(configured()[1]))[2]


def test_pinned_planning_hours(database, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    with Session(database) as db:
        schedules = list(db.execute(text("SELECT poi_id,schedule FROM poi_normalized_hours WHERE dataset_version=:v"), {"v":V}).mappings())
        monkeypatch.setenv("DATABASE_URL", str(database.url))
        planning = load_module("deployment_planning_test", "services/planning_data/main.py")
        try:
            ids = [r["poi_id"] for r in schedules]
            days = [date(2026,11,23+i) for i in range(7)]
            actual = planning._load_operating(V, ids, days)
            assert not planning._load_operating("historical", ids, days)
            for r in schedules:
                schedule = json.loads(r["schedule"])
                for d in days:
                    fact = actual[r["poi_id"]]["operations"][d.isoformat()]["openings"]
                    assert fact["source"].startswith("normalized-hours:hours-"+V)
                    assert fact["value"] == [{"start_min":a,"end_min":b} for a,b in schedule["weekly"][str((d.weekday()+1)%7)]]
        finally:
            planning.engine.dispose()



def test_fresh_install_staged_activation_and_atomic_failure(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "fresh.db"))
    Base.metadata.create_all(engine)
    import app.deployment as installer
    real_validate = installer.validate
    with Session(engine) as db:
        def fail(*args, **kwargs):
            raise ValueError("injected readback failure")
        monkeypatch.setattr(installer, "validate", fail)
        with pytest.raises(ValueError, match="injected"):
            initialize(db,activate=True)
        db.rollback()
        assert db.execute(text("SELECT count(*) FROM pois")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM dataset_versions")).scalar_one() == 0
        monkeypatch.setattr(installer, "validate", real_validate)
        assert initialize(db)["active_dataset"] is None
        assert initialize(db)["active_dataset"] is None
        assert initialize(db,activate=True)["active_dataset"] == V
        assert check_startup(db) == 0
    engine.dispose()

def test_configuration_version_mismatch(monkeypatch):
    monkeypatch.setenv("PLACES_VERSION", "wrong")
    with pytest.raises(ValueError,match="version mismatch"):
        configured()

@pytest.mark.skipif(os.getenv("TRAVELSYNC_REAL_E5_INTEGRATION") != "1", reason="requires actual prewarmed pinned E5 cache")
def test_actual_e5_recommendation_uses_deployed_cache(database, monkeypatch):
    monkeypatch.setenv("TRAVELSYNC_E5_REGENERATE","0")
    recommender = load_module("deployment_recommender_test", "services/recommender/main.py")
    with Session(database) as db:
        rows = [dict(place_id=p.id,dataset_version=V,place_name=p.name,category_name=p.category,latitude=p.lat,longitude=p.lng,enriched_metadata=p.enriched_metadata,**p.features)
                for p in db.scalars(select(Poi).where(Poi.dataset_version==V)).unique()]
    response = recommender.recommend(dict(dataset_version=V,trip_id="deployment-verification",poi_snapshot=rows,
        members=[dict(user_id="public-probe", ratings=dict(zip(FEATURE_NAMES,[5,4,2,3,4,2,5,2,4,1])))],options={"group_k":5}))
    assert len(response["group"]) == 5
    member = response["scoring"]["members"]["public-probe"]
    assert member["effective_scoring_method"] == "e5_hybrid" and member["fallback_reason"] is None
    expected_identity = catalogue_identity(load_places(configured()[1]))[2]
    assert response["scoring"]["cache_identity"] == {k:v for k,v in expected_identity.items() if k != "poi_ids"}
    assert response["individual"]["public-probe"][0]["semantic_score_raw"] is not None
