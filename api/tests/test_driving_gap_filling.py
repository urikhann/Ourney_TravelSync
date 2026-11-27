"""Real API, driving orchestrator, routing adapter and solver with synthetic provider facts."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app import config, drive_planning, services
from ourney_travelsync.optimizer.contracts import PlanRequest, PlanResponse
from ourney_travelsync.optimizer.validation import validate_final
from .test_optimizer_integration import audits, c, configuration, generate, ready


@pytest.fixture
def driving_gap(monkeypatch, planning_adapter, tmp_path):
    monkeypatch.setenv("ROUTE_CACHE_PATH", str(tmp_path / "gap-routes.sqlite3"))
    path = Path(__file__).resolve().parents[2] / "services/planning_data/main.py"
    spec = importlib.util.spec_from_file_location("gap_kakao_adapter", path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    state = SimpleNamespace(outcome="success", duration=180, requests=[], solves=[], provider_calls=0)
    stamp = "2026-11-01T00:00:00Z"

    def fact(value):
        return dict(value=value, status="KNOWN", source="synthetic driving gap fixture", verified_at=stamp)

    def operating(version, ids, days):
        records = {}
        for pid in ids:
            window = dict(start_min=480, end_min=1320)
            if pid == "gwangalli":
                window = dict(start_min=665, end_min=665 + state.duration)
                if state.outcome == "opening_conflict":
                    window = dict(start_min=900, end_min=1080)
            records[pid] = dict(poi_id=pid, operations={day.isoformat(): dict(
                openings=fact([window]), closed=fact(pid not in {"bmuseum", "xsky", "gwangalli"}
                    or (pid == "gwangalli" and state.outcome == "closed")),
                event_active=fact(True)) for day in days})
        return records

    real_client = httpx.AsyncClient

    def provider(request):
        state.provider_calls += 1
        extra = len(state.requests) > 2
        if extra and state.outcome == "quota":
            return httpx.Response(429, json={"code": -10, "msg": "synthetic quota exhausted"})
        if extra and state.outcome == "unknown":
            return httpx.Response(200, json={"routes": []})
        if extra and state.outcome == "unreachable":
            return httpx.Response(200, json={"routes": [dict(result_code=104)]})
        duration = 2400 if extra and state.outcome == "slow" else 300
        return httpx.Response(200, json={"routes": [dict(result_code=0,
            summary=dict(duration=duration, distance=500, fare=dict(toll=0)))]})

    class ProviderClient:
        def __init__(self, **kwargs):
            self.client = real_client(transport=httpx.MockTransport(provider), **kwargs)

        async def __aenter__(self):
            return await self.client.__aenter__()

        async def __aexit__(self, *args):
            return await self.client.__aexit__(*args)

    real_planning = services.planning_data

    async def planning(payload, *, selected=False):
        state.requests.append(payload)
        if len(state.requests) > 2 and state.outcome == "provider_error":
            raise HTTPException(503, "planning_data_unavailable")
        response = await real_planning(payload, selected=selected)
        if len(state.requests) > 2 and state.outcome == "invalid_response":
            response["routing_version"] = "wrong-version"
        return response

    real_build = services.build_plans

    async def build(payload):
        if payload["gap_target"] and state.outcome == "solver_timeout":
            response = PlanResponse(run_id=payload["run_id"], input_snapshot=payload,
                                    solver_status="UNKNOWN", error_code="SEARCH_TIMEOUT").model_dump(mode="json")
        else:
            response = await real_build(payload)
        if payload["gap_target"] and state.outcome == "tampered":
            response["days"][0]["items"][1]["start_min"] = 900
        state.solves.append((payload, response))
        return response

    real_estimates = drive_planning.draft_edges

    def estimates(req, context):
        # The geographic draft is slower than the provider. C cannot fit its
        # opening interval in that draft but fits once both new legs are verified.
        for edge in real_estimates(req, context):
            yield edge.model_copy(update={"duration_minutes": 20})

    monkeypatch.setattr(adapter, "httpx", SimpleNamespace(AsyncClient=ProviderClient,
        Timeout=httpx.Timeout, HTTPError=httpx.HTTPError))
    monkeypatch.setattr(adapter, "KAKAO_REST_API_KEY", "synthetic-key")
    monkeypatch.setattr(adapter, "PLANNING_DATA_TOKEN", "")
    monkeypatch.setattr(adapter, "_load_operating", operating)
    monkeypatch.setattr(config, "OPTIMIZER_SEARCH_SECONDS", 10)
    monkeypatch.setitem(services.TEST_TRANSPORTS, config.PLANNING_DATA_URL, httpx.ASGITransport(app=adapter.app))
    monkeypatch.setattr(services, "planning_data", planning)
    monkeypatch.setattr(services, "build_plans", build)
    monkeypatch.setattr(drive_planning, "draft_edges", estimates)
    yield state
    adapter.engine.dispose()


def request_plan(state, *, enabled=True, rest=False):
    headers, trip_id, _ = ready(individual_method="feature_mean")
    body = configuration()
    day = body["days"][0]["date"]
    body.update(transport_mode="drive", budget_total=0,
                gap_policy=dict(enabled=enabled),
                daily_composition=dict(min_activities=2, min_activity_categories=2, meal_windows=[]),
                duration_overrides=dict(bmuseum=60, xsky=30, gwangalli=state.duration),
                locked_items=[dict(poi_id="bmuseum", date=day, start_min=600),
                              dict(poi_id="xsky", date=day, start_min=670 + state.duration)])
    if rest:
        body["days"][0]["breaks"] = [dict(start_min=680, end_min=850)]
    response = generate(headers, trip_id, body)
    assert response.status_code == 200, response.text
    plan = response.json()["plans"][0]
    audit = audits(trip_id)[0]
    baseline = next(result for req, result in state.solves if not req["provisional"])
    original = baseline["days"][0]["items"]
    assert {v["poi_id"] for v in original} == {"bmuseum", "xsky"}
    assert sum(req["search_seconds"] for req, _ in state.solves) <= config.OPTIMIZER_SEARCH_SECONDS
    req = PlanRequest.model_validate(audit.optimizer_request)
    assert "gwangalli" in {p.poi_id for p in req.candidates}, [p.poi_id for p in req.candidates]
    result = PlanResponse.model_validate(audit.response)
    assert req == result.input_snapshot
    assert validate_final(req, result.days).passed
    assert not req.enforce_cost
    assert all(edge.status != "ESTIMATED" for edge in req.edges)
    visits = {v["poi_id"]: v for v in plan["days"][0]["items"]}
    assert visits["bmuseum"]["start_min"] == 600
    assert visits["xsky"]["start_min"] == 670 + state.duration
    assert len(visits) == len(plan["days"][0]["items"])
    assert c.get(f"/trips/{trip_id}/itineraries", headers=headers).json()["plans"][0]["days"] == plan["days"]
    return plan, audit, original


@pytest.mark.parametrize("duration", [60, 180])
def test_driving_gap_fetches_missing_legs_and_inserts_activity(driving_gap, duration):
    driving_gap.duration = duration
    plan, audit, original = request_plan(driving_gap)
    assert len(driving_gap.requests) == 3
    repair = audit.planning_data["gap_filling"]
    assert repair["status"] == "APPLIED"
    assert len(repair["requested_legs"]) == 2
    assert {(leg["origin"], leg["destination"]) for leg in repair["requested_legs"]} == {
        ("bmuseum", "gwangalli"), ("gwangalli", "xsky")}
    assert all(leg["origin"] != "gwangalli" and leg["destination"] != "gwangalli"
               for leg in driving_gap.requests[1]["route_legs"])
    assert driving_gap.provider_calls == 5
    visits = plan["days"][0]["items"]
    assert [v["poi_id"] for v in visits] == ["bmuseum", "gwangalli", "xsky"]
    assert visits[1]["start_min"] == 665 and visits[1]["departure_min"] == 665 + duration
    assert not visits[1]["locked"]
    assert plan["stats"]["waiting_minutes"] == 0
    assert original[1]["start_min"] - original[1]["arrival_min"] == duration + 5
    assert audit.response["metrics"]["gap_fill_applied"]
    assert len(audit.response["attempts"]) >= 2
    assert repair["route_response"]["routing_attempts"]["point"] == driving_gap.provider_calls


@pytest.mark.parametrize("outcome", ["unknown", "unreachable", "slow", "provider_error", "invalid_response",
                                     "opening_conflict", "quota", "solver_timeout", "tampered"])
def test_failed_driving_gap_repair_preserves_original(driving_gap, outcome):
    driving_gap.outcome = outcome
    plan, audit, original = request_plan(driving_gap)
    assert len(driving_gap.requests) == 3
    assert audit.planning_data["gap_filling"]["status"] == "RETAINED"
    assert [v["poi_id"] for v in plan["days"][0]["items"]] == [v["poi_id"] for v in original]
    assert [(v["start_min"], v["departure_min"]) for v in plan["days"][0]["items"]] == [
        (v["start_min"], v["departure_min"]) for v in original]
    assert plan["stats"]["waiting_minutes"] == 185
    assert audit.status == "PASSED"
    if outcome == "quota":
        assert plan["stats"]["routing_warnings"]


@pytest.mark.parametrize("enabled,rest", [(False, False), (True, True)])
def test_disabled_or_intentional_gap_avoids_extra_routing(driving_gap, enabled, rest):
    plan, audit, _ = request_plan(driving_gap, enabled=enabled, rest=rest)
    assert len(driving_gap.requests) == 2
    assert "gap_filling" not in audit.planning_data
    assert len(plan["days"][0]["items"]) == 2


def test_no_eligible_activity_retains_plan_without_fetching_more_routes(driving_gap):
    driving_gap.outcome = "closed"
    plan, audit, _ = request_plan(driving_gap)
    assert len(driving_gap.requests) == 2
    assert audit.planning_data["gap_filling"]["status"] == "RETAINED"
    assert len(plan["days"][0]["items"]) == 2
