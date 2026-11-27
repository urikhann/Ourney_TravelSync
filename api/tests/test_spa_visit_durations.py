import pytest
from sqlalchemy import select

from app import services
from app.db import SessionLocal
from app.models import Poi
from ourney_travelsync.visit_durations import RULE_VERSION
from .test_optimizer_integration import ready, generate, configuration, audits, c, stable_optimizer_seed


@pytest.mark.parametrize("name", ["테스트 온천", "테스트 찜질방"])
@pytest.mark.parametrize("override", [None, 75])
def test_spa_duration_reaches_catalogue_retrieval_and_validated_plan(compact_planning_adapter, monkeypatch, name, override):
    h, tid, _ = ready(individual_method="feature_mean")
    with SessionLocal() as db:
        place = db.scalar(select(Poi).where(Poi.id == "bmuseum"))
        version, old_name, old_duration = place.dataset_version, place.name, place.duration_min
        place.name = name
        db.commit()
    try:
        options = c.get(f"/trips/{tid}/planning-places", params={"q": name}, headers=h)
        assert options.status_code == 200, options.text
        assert next(p for p in options.json() if p["id"] == "bmuseum")["duration_min"] == 180
        detail = c.get("/pois/bmuseum", headers=h)
        assert detail.status_code == 200, detail.text
        assert detail.json()["duration_min"] == 180
        captured = []
        original = services.planning_candidates

        async def candidates(payload):
            captured.append(payload)
            return await original(payload)

        monkeypatch.setattr(services, "planning_candidates", candidates)
        body = configuration()
        body["locked_items"] = [dict(poi_id="bmuseum", date="2026-11-06")]
        if override is not None:
            body["duration_overrides"] = {"bmuseum": override}
        response = generate(h, tid, body)
        assert response.status_code == 200, response.text
        minutes = override if override is not None else 180
        snapshot = next(p for p in captured[0]["poi_snapshot"] if p["place_id"] == "bmuseum")
        assert snapshot["duration_min"] == minutes
        plan = response.json()["plans"][0]
        visit = next(i for d in plan["days"] for i in d["items"] if i["poi_id"] == "bmuseum")
        assert visit["stay_minutes"] == minutes
        source = "user-duration-override" if override is not None else "place-name-default"
        assert visit["duration_source"] == source
        candidate = next(p for p in audits(tid)[-1].optimizer_request["candidates"] if p["poi_id"] == "bmuseum")
        assert candidate["duration_min"]["value"] == minutes
        assert candidate["duration_min"]["status"] == "ESTIMATED"
        assert candidate["duration_min"]["rule_version"] == RULE_VERSION
        assert plan["stats"]["validation_status"] == "PASSED"
    finally:
        with SessionLocal() as db:
            place = db.get(Poi, ("bmuseum", version))
            assert place.duration_min == old_duration
            place.name = old_name
            db.commit()
