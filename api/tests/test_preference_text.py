"""Optional text is versioned and forwarded unchanged to the recommender."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Preference, RecommendationRun
from .test_flow import FEATURES, signup


def test_existing_api_database_gets_empty_text_without_changing_saved_ratings(monkeypatch):
    from sqlalchemy import create_engine, text
    from app import db as database
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE pois (id TEXT PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE preferences (id INTEGER PRIMARY KEY, ratings TEXT)'))
        conn.execute(text("INSERT INTO preferences VALUES (1, 'existing ratings')"))
    monkeypatch.setattr(database, 'engine', engine)
    database.ensure_enriched_metadata_column()
    database.ensure_enriched_metadata_column()  # additive compatibility is idempotent
    with engine.connect() as conn:
        row = conn.execute(text('SELECT ratings, preference_text FROM preferences')).one()
        assert tuple(row) == ('existing ratings', '')
    engine.dispose()


def test_optional_text_roundtrip_versions_and_request_snapshot():
    client = TestClient(app)
    headers = signup("TextQuery")
    trip = client.post("/trips", headers=headers, json={
        "name": "Text test", "city": "busan", "start_date": "2026-11-01", "end_date": "2026-11-02"
    }).json()
    path = f"/trips/{trip['id']}/preferences/me"
    assert client.get(path, headers=headers).json()["preference_text"] == ""
    ratings = dict.fromkeys(FEATURES, 3)
    assert client.put(path, headers=headers, json={"ratings": ratings}).json()["preference_text"] == ""
    query = "떡볶이를 먹고 바닷가 산책"
    response = client.put(path, headers=headers, json={
        "ratings": ratings, "preference_text": query, "expected_version": 1
    })
    assert response.status_code == 200 and response.json()["version"] == 2
    assert client.get(path, headers=headers).json()["preference_text"] == query
    assert client.put(path, headers=headers, json={"ratings": ratings, "preference_text": None}).status_code == 422
    run = client.post(f"/trips/{trip['id']}/recommendations", headers=headers)
    assert run.status_code == 200, run.text
    with SessionLocal() as db:
        saved = db.scalars(select(Preference).where(Preference.trip_id == trip["id"]).order_by(Preference.version)).all()
        assert [(p.version, p.preference_text) for p in saved] == [(1, ""), (2, query)]
        snapshot = db.scalar(select(RecommendationRun).where(RecommendationRun.trip_id == trip["id"]))
        assert snapshot.request["members"][0]["preference_text"] == query
