"""Authenticated API → trusted planning-data adapter → real CP-SAT → local E3 → DB."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, services
from app.db import SessionLocal
from app.main import app
from app.models import Itinerary, OptimizationRun, Preference
from .test_flow import signup, FEATURES

c = TestClient(app)


def configuration(days=("2026-11-06",)):
    point = dict(label="Explicit fixture endpoint", latitude=35.15, longitude=129.1)
    return dict(days=[dict(date=d, start_min=540, end_min=1080, start_point=point, end_point=point) for d in days],
                budget_total=10000, transport_mode="walk")


def ready(*, individual_method=None, ratings=None):
    h = signup("Opt" + uuid4().hex[:10])
    trip = c.post("/trips", headers=h, json=dict(name="Optimizer integration", start_date="2026-11-06",
                                                end_date="2026-11-06")).json()
    tid = trip["id"]
    c.put(f"/trips/{tid}/preferences/me", headers=h, json={"ratings": ratings or dict.fromkeys(FEATURES, 3)})
    c.put(f"/trips/{tid}/availability", headers=h, json={"dates": ["2026-11-06"]})
    options = {"group_k": 3}
    if individual_method is not None:
        options["individual_method"] = individual_method
    response = c.post(f"/trips/{tid}/recommendations", headers=h, json=options)
    assert response.status_code == 200, response.text
    rec = response.json()
    return h, tid, rec


def generate(h, tid, body=None):
    return c.post(f"/trips/{tid}/itineraries/generate", headers=h, json=configuration() if body is None else body)


def audits(tid):
    with SessionLocal() as db:
        return db.scalars(select(OptimizationRun).where(OptimizationRun.trip_id == tid).order_by(OptimizationRun.id)).all()


@pytest.mark.parametrize("individual_method", [None, "e5_hybrid", "tfidf", "feature_mean"])
def test_auth_to_real_solver_persists_private_replay_and_public_return_leg(planning_adapter, monkeypatch, individual_method):
    import numpy as np
    from ourney_travelsync.recommenders import hybrid

    def embeddings(places, config):
        rng = np.random.default_rng(17)
        vectors = rng.normal(size=(len(places.place_ids) + 10, 384)).astype("float32")
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors[:-10], vectors[-10:], {"dataset_content_hash": "synthetic optimizer integration"}

    monkeypatch.setattr(hybrid, "embeddings", embeddings)
    ratings = dict.fromkeys(FEATURES, 3)
    ratings["nature"] = 5
    h, tid, rec = ready(individual_method=individual_method, ratings=ratings)
    res = generate(h, tid)
    assert res.status_code == 200, res.text
    plan = res.json()["plans"][0]
    assert plan["variant"] == "optimized" and plan["stats"]["validation_status"] == "PASSED"
    assert plan["days"][0]["return_travel_minutes"] == 10
    assert plan["days"][0]["items"][0]["arrival"]
    assert plan["stats"]["total_cost"] > 0
    if individual_method in (None, "e5_hybrid"):
        assert rec["score_scale"] == "relative_relevance_0_1"
        assert rec["individual_method"] == "e5_hybrid"
        assert all(m["effective_scoring_method"] == "e5_hybrid" for m in rec["scoring"]["members"].values())
        assert plan["stats"]["group_fit"] is None
        assert all(item["mean_score"] is None for day in plan["days"] for item in day["items"])
        assert all(row["fit"] is None for row in rec["group"])
        assert all(value is None for value in rec["metrics"]["member_satisfaction"].values())
    assert "input_snapshot" not in res.text and "personal_scores" not in res.text
    audit = audits(tid)[0]
    assert audit.status == "PASSED" and audit.itinerary_id == plan["id"]
    assert audit.response["validation_status"] == "PASSED"
    assert audit.optimizer_request["dataset_version"] == rec["dataset_version"]
    assert audit.optimizer_request == audit.response["input_snapshot"]
    assert audit.optimizer_request["budget_total"] == 10000
    source = {p["poi_id"]: p for p in audit.input["candidate_retrieval"]["group"]}
    for candidate in audit.optimizer_request["candidates"]:
        row = source[candidate["poi_id"]]
        expected = {u: (v - 1) / 4 if individual_method == "feature_mean" else v
                    for u, v in row["member_scores"].items()}
        assert candidate["personal_scores"] == expected
        assert candidate["relevance"] == row["member_relevance"]
    assert planning_adapter.calls[0]["group_size"] == 1
    assert c.get(f"/trips/{tid}/itineraries", headers=h).json()["plans"][0] == plan


def test_no_adapter_fails_without_greedy_fallback(monkeypatch):
    h, tid, _ = ready()
    monkeypatch.setattr(config, "PLANNING_DATA_URL", "")
    res = generate(h, tid)
    assert res.status_code == 503 and res.json()["detail"] == "planning_data_not_configured"
    assert audits(tid)[0].status == "FAILED"
    assert c.get(f"/trips/{tid}/itineraries", headers=h).json()["plans"] == []


@pytest.mark.parametrize("missing,reason", [
    ("duration_min", "DURATION_UNKNOWN"),
    ("route_duration", "ROUTE_UNAVAILABLE"),
    ("route_fare", "ROUTE_UNAVAILABLE"),
])
def test_missing_planning_evidence_keeps_previous_plan_and_votes(planning_adapter, missing, reason):
    h, tid, _ = ready()
    old = generate(h, tid).json()["plans"][0]
    c.post(f"/itineraries/{old['id']}/vote", headers=h)
    def unknown(req, response):
        for p in response["pois"]:
            if missing in ("cost_per_person", "duration_min"):
                p[missing] = {}
            elif missing in ("openings", "closed"):
                for op in p["operations"].values():
                    op[missing] = {}
        if missing.startswith("route_"):
            field = "duration_minutes" if missing == "route_duration" else "group_fare"
            for edge in response["edges"]:
                edge[field] = None
        return response
    planning_adapter.transform = unknown
    failed = generate(h, tid)
    assert failed.status_code == 409 and failed.json()["detail"] == "optimizer_insufficient_candidates"
    view = c.get(f"/trips/{tid}/itineraries", headers=h).json()
    assert view["plans"][0]["id"] == old["id"] and view["voted"] == 1
    audit = audits(tid)[-1]
    assert audit.status == "FAILED" and audit.itinerary_id is None
    assert all(not row["schedule_eligible"] for row in audit.response["eligibility"])
    assert all(any(reason in value for value in row["reasons"]) for row in audit.response["eligibility"])


@pytest.mark.parametrize("field", ["context_id", "dataset_version", "transport_mode"])
def test_wrong_adapter_snapshot_is_rejected(planning_adapter, field):
    h, tid, _ = ready()
    def wrong(req, response):
        response[field] = "other"
        return response
    planning_adapter.transform = wrong
    assert generate(h, tid).json()["detail"] == "planning_data_invalid_response"
    assert audits(tid)[0].status == "FAILED"


def test_service_claiming_passed_cannot_bypass_backend_e3(monkeypatch, planning_adapter):
    h, tid, _ = ready()
    old = generate(h, tid).json()["plans"][0]
    assert c.post(f"/itineraries/{old['id']}/vote", headers=h).status_code == 200
    original = services.build_plans
    async def forged(payload):
        result = await original(payload)
        result["days"][0]["items"][0]["arrival_min"] += 1
        return result
    monkeypatch.setattr(services, "build_plans", forged)
    res = generate(h, tid)
    assert res.status_code == 502 and res.json()["detail"] == "optimizer_final_validation_failed"
    view = c.get(f"/trips/{tid}/itineraries", headers=h).json()
    assert view["plans"][0]["id"] == old["id"] and view["voted"] == 1
    assert audits(tid)[-1].status == "FAILED"


def test_changed_preferences_during_search_cannot_publish(monkeypatch, planning_adapter):
    h, tid, _ = ready()
    original = services.build_plans
    async def changed(payload):
        result = await original(payload)
        with SessionLocal() as db:
            old = db.scalar(select(Preference).where(Preference.trip_id == tid))
            db.add(Preference(trip_id=tid, user_id=old.user_id, version=2, ratings=old.ratings))
            db.commit()
        return result
    monkeypatch.setattr(services, "build_plans", changed)
    res = generate(h, tid)
    assert res.status_code == 409 and res.json()["detail"] == "optimizer_inputs_changed"
    assert audits(tid)[-1].status == "FAILED"


def test_owner_dates_budget_and_unknown_body_data_are_enforced(planning_adapter):
    h, tid, _ = ready()
    other = signup("OptOther" + uuid4().hex[:8])
    code = c.get(f"/trips/{tid}", headers=h).json()["invite_code"]
    c.post(f"/trips/join/{code}", headers=other)
    assert generate(other, tid).status_code == 403
    assert generate(h, tid).json()["detail"] == "recommendations_stale"
    assert c.post(f"/trips/{tid}/itineraries/generate", headers=h).status_code == 422
    body = configuration()
    body["budget_total"] = None
    assert generate(h, tid, body).status_code == 422
    body = configuration()
    body["candidates"] = []  # clients cannot inject evidence or recommendation scores
    assert generate(h, tid, body).status_code == 422


def test_legacy_rows_are_not_published_or_votable():
    h, tid, rec = ready()
    with SessionLocal() as db:
        row = Itinerary(trip_id=tid, run_id=rec["run_id"], letter="A", variant="best", days=[], stats={})
        db.add(row)
        db.commit()
        pid = row.id
    assert not c.get(f"/trips/{tid}/itineraries", headers=h).json()["plans"]
    assert c.post(f"/itineraries/{pid}/vote", headers=h).status_code == 409


def test_adapter_auth_is_header_only(monkeypatch, planning_adapter):
    import httpx
    h, tid, _ = ready()
    seen = []
    original = services.TEST_TRANSPORTS[config.PLANNING_DATA_URL]
    class HeaderTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen.append(request.headers.get("authorization"))
            return await original.handle_async_request(request)
    monkeypatch.setattr(config, "PLANNING_DATA_TOKEN", "synthetic-auth-fixture")
    monkeypatch.setitem(services.TEST_TRANSPORTS, config.PLANNING_DATA_URL, HeaderTransport())
    assert generate(h, tid).status_code == 200
    assert seen == ["Bearer synthetic-auth-fixture"]
    assert "synthetic-auth-fixture" not in str(audits(tid)[0].planning_data)


@pytest.mark.parametrize("change,code", [("availability", "optimizer_member_unavailable"),
                                         ("preference", "recommendations_stale"),
                                         ("relevance", "optimizer_recommendation_data_missing"),
                                         ("locks", "optimizer_invalid_locks")])
def test_invalid_or_stale_inputs_stop_before_adapter(planning_adapter, change, code):
    from app.models import RecommendationRun
    h, tid, rec = ready()
    body = configuration()
    if change == "availability":
        c.put(f"/trips/{tid}/availability", headers=h, json={"dates": []})
    elif change == "preference":
        c.put(f"/trips/{tid}/preferences/me", headers=h, json={"ratings": dict.fromkeys(FEATURES, 4)})
    elif change == "relevance":
        with SessionLocal() as db:
            run = db.get(RecommendationRun, rec["run_id"])
            response = dict(run.response)
            response.pop("relevance_version")
            run.response = response
            db.commit()
    elif change == "locks":
        body["locked_items"] = [{"poi_id": "unknown", "date": "2026-11-06"}]
    assert generate(h, tid, body).json()["detail"] == code
    assert not planning_adapter.calls


def test_saved_snapshot_is_preserved_but_not_approvable_after_member_change(planning_adapter):
    h, tid, _ = ready()
    old = generate(h, tid).json()["plans"][0]
    assert not old["stale"]
    c.put(f"/trips/{tid}/preferences/me", headers=h, json={"ratings": dict.fromkeys(FEATURES, 4)})
    view = c.get(f"/trips/{tid}/itineraries", headers=h).json()
    assert view["plans"][0]["id"] == old["id"] and view["plans"][0]["stale"]
    assert c.post(f"/itineraries/{old['id']}/vote", headers=h).json()["detail"] == "optimizer_inputs_changed"


def test_missing_place_cost_does_not_block_generation(planning_adapter):
    h, tid, _ = ready()
    def unknown_cost(req, response):
        for p in response["pois"]:
            p["cost_per_person"] = {}
        return response
    planning_adapter.transform = unknown_cost
    response = generate(h, tid)
    assert response.status_code == 200, response.text
    plan = response.json()["plans"][0]
    assert plan["stats"]["validation_status"] == "PASSED"
    assert all(item["visit_cost"] == 0 for day in plan["days"] for item in day["items"])
    assert all(p["cost_per_person"]["status"] == "UNKNOWN" for p in audits(tid)[-1].optimizer_request["candidates"])
