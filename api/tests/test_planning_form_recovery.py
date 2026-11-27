"""LALALA regression shape using synthetic test data; no production DB writes."""
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import select, delete

from app import services
from app.db import SessionLocal
from app.models import Itinerary, OptimizationRun, Poi
from app.lock_policy import planning_configuration
from app.features import FEATURES
from .test_optimizer_integration import c, ready, generate, configuration, audits
from .test_proposal_approval import replacement_fixture_clock


def add_synthetic_cafes(tid, request):
    with SessionLocal() as db:
        from app.models import RecommendationRun
        run = db.scalar(select(RecommendationRun).where(RecommendationRun.trip_id == tid))
        ids = [f"synthetic-locked-cafe-{tid}-{i}" for i in range(3)]
        for pid in ids:
            db.add(Poi(id=pid, dataset_version=run.dataset_version, name="Synthetic cafe fixture", category="카페",
                       features={**dict.fromkeys(FEATURES, 0), "cafe": 1}, lat=35.15, lng=129.1, tags=[], source="synthetic test fixture"))
        db.commit()
    def cleanup():
        with SessionLocal() as db:
            db.execute(delete(Poi).where(Poi.id.in_(ids), Poi.source == "synthetic test fixture"))
            db.commit()
    request.addfinalizer(cleanup)
    return ids


def test_three_mandatory_cafes_fail_preflight_with_specific_conflict(planning_adapter, replacement_fixture_clock, monkeypatch, request):
    h, tid, _ = ready(individual_method="feature_mean")
    ids = add_synthetic_cafes(tid, request)
    body = configuration()
    body["locked_items"] = [dict(poi_id=pid, date=body["days"][0]["date"]) for pid in ids]
    async def forbidden(*a, **kw):
        raise AssertionError("Impossible lock counts must be rejected before external work")
    monkeypatch.setattr(services, "planning_candidates", forbidden)
    monkeypatch.setattr(services, "planning_data", forbidden)
    monkeypatch.setattr(services, "build_plans", forbidden)
    result = generate(h, tid, body)
    assert result.status_code == 422, result.text
    detail = result.json()["detail"]
    assert detail["code"] == "optimizer_lock_composition_conflict"
    assert detail["conflicts"][0] == dict(date=body["days"][0]["date"], rule="max_cafes", limit=2,
                                            locked_count=3, poi_ids=ids)
    assert len(body["locked_items"]) == 3, "Incoming locks must never be silently removed"
    assert not audits(tid)


def test_failed_attempt_cannot_replace_saved_explicit_lock_defaults(planning_adapter, replacement_fixture_clock, request):
    h, tid, _ = ready(individual_method="feature_mean")
    body = configuration()
    body["locked_items"] = [dict(poi_id="bmuseum", date=body["days"][0]["date"], start_min=600)]
    published = generate(h, tid, body)
    assert published.status_code == 200, published.text
    plan = published.json()["plans"][0]
    ids = add_synthetic_cafes(tid, request)
    with SessionLocal() as db:
        base = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == plan["id"]))
        original = deepcopy(db.get(Itinerary, plan["id"]).days)
        failed_input = deepcopy(base.input)
        failed_input["configuration"]["locked_items"] += [dict(poi_id=pid, date=body["days"][0]["date"]) for pid in ids]
        failed_input["configuration"]["max_leg_minutes"] = 75
        db.add(OptimizationRun(trip_id=tid, recommendation_run_id=base.recommendation_run_id,
                              requested_by=base.requested_by, status="FAILED", input=failed_input,
                              error_code="optimizer_no_feasible_itinerary"))
        db.commit()
    view = c.get(f"/trips/{tid}/itineraries", headers=h).json()
    assert len(view["latest_optimization"]["configuration"]["locked_items"]) == 4
    assert view["setup"]["configuration"]["locked_items"] == [dict(poi_id="bmuseum", date=body["days"][0]["date"], start_min=600)]
    assert view["setup"]["configuration"]["max_leg_minutes"] == 75
    with SessionLocal() as db:
        assert db.get(Itinerary, plan["id"]).days == original


def test_initial_failed_form_preserves_user_input_without_a_saved_plan():
    requested = dict(locked_items=[dict(poi_id="explicit", date="2026-11-30")], max_leg_minutes=15)
    result = planning_configuration(None, None, SimpleNamespace(input=dict(configuration=requested)), None, None)
    assert result == requested and result is not requested


def test_initial_driving_draft_failure_keeps_replay_and_solver_status(planning_adapter, replacement_fixture_clock, monkeypatch):
    h, tid, _ = ready(individual_method="feature_mean")
    def selected(request, response):
        wanted = {(x["date"], x["origin"], x["destination"]) for x in request.get("route_legs", [])}
        response["edges"] = [e for e in response["edges"] if (e["date"], e["origin"], e["destination"]) in wanted]
        response["routing_attempts"] = request.get("routing_attempts", {})
        return response
    planning_adapter.transform = selected
    async def infeasible(payload):
        assert payload["provisional"]
        return dict(run_id=payload["run_id"], input_snapshot=payload, solver_status="INFEASIBLE", error_code="NO_FEASIBLE_ITINERARY")
    monkeypatch.setattr(services, "build_plans", infeasible)
    body = {**configuration(), "transport_mode": "drive"}
    response = generate(h, tid, body)
    assert response.status_code == 409, response.text
    audit = audits(tid)[0]
    assert audit.status == "FAILED" and audit.optimizer_request["provisional"]
    assert audit.response["solver_status"] == "INFEASIBLE"
    assert audit.planning_data["stage"] == "draft"
    view = c.get(f"/trips/{tid}/itineraries", headers=h).json()
    assert view["latest_optimization"]["solver_status"] == "INFEASIBLE"
