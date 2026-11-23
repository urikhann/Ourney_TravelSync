"""Actual Kakao adapter -> CP-SAT -> E3, with synthetic DB/provider evidence."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app import config, services
from .test_optimizer_integration import ready, generate, configuration, audits


@pytest.mark.parametrize(("repair_needed", "day_count", "breakfast"),
                         [(False, 1, False), (True, 1, False), (False, 2, False), (False, 2, True)])
def test_kakao_adapter_to_optimizer_and_e3(monkeypatch, planning_adapter, tmp_path, repair_needed, day_count, breakfast):
    monkeypatch.setenv("ROUTE_CACHE_PATH", str(tmp_path / "routes.sqlite3"))
    path = Path(__file__).resolve().parents[2] / "services/planning_data/main.py"
    spec = importlib.util.spec_from_file_location("kakao_integration", path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    stamp = datetime.now(timezone.utc).isoformat()

    def fact(value):
        return dict(value=value, status="KNOWN", source="synthetic fixture", verified_at=stamp)

    def operating(version, ids, days):
        return {pid: dict(poi_id=pid, duration_min=fact(30), cost_per_person=fact(100),
                         operations={d.isoformat(): dict(openings=fact([dict(start_min=480, end_min=1320)]),
                                                         closed=fact(False), event_active=fact(True)) for d in days})
                for pid in ids}

    # Exercise the real selected-leg adapter, replacing only the provider.
    real_client = httpx.AsyncClient

    calls = []
    def respond(req):
        assert req.method == "GET"
        assert req.url.params["avoid"] == "toll"
        assert req.headers["Authorization"] == "KakaoAK synthetic-key"
        pair = req.url.params["origin"], req.url.params["destination"]
        assert pair not in calls
        calls.append(pair)
        # A draft that gets faster keeps its order. The repair case makes
        # one selected leg impossible and keeps the remaining legs short.
        duration = 86400 if repair_needed and len(calls) == 1 else 60
        return httpx.Response(200, json={"routes": [dict(result_code=0,
            summary=dict(duration=duration, distance=500, fare=dict(toll=0)))]})

    class ProviderClient:
        def __init__(self, **kwargs):
            self.client = real_client(transport=httpx.MockTransport(respond), **kwargs)
        async def __aenter__(self):
            return await self.client.__aenter__()
        async def __aexit__(self, *args):
            return await self.client.__aexit__(*args)

    # Separate module binding avoids replacing the API's httpx.AsyncClient.
    from types import SimpleNamespace
    monkeypatch.setattr(adapter, "httpx", SimpleNamespace(AsyncClient=ProviderClient,
        Timeout=httpx.Timeout, HTTPError=httpx.HTTPError))
    monkeypatch.setattr(adapter, "KAKAO_REST_API_KEY", "synthetic-key")
    monkeypatch.setattr(adapter, "PLANNING_DATA_TOKEN", "")
    monkeypatch.setattr(adapter, "_load_operating", operating)
    monkeypatch.setattr(config, "OPTIMIZER_SEARCH_SECONDS", 20)
    monkeypatch.setitem(services.TEST_TRANSPORTS, config.PLANNING_DATA_URL, httpx.ASGITransport(app=adapter.app))
    try:
        dates = ("2026-11-06", "2026-11-07")[:day_count]
        headers, tid, _ = ready(individual_method="feature_mean", days=dates)
        body = configuration(dates)
        if day_count == 2:
            body["days"][1]["start_point"] = dict(label="Second day start", latitude=35.16, longitude=129.11)
        body["transport_mode"] = "drive"
        if breakfast:
            body["daily_composition"] = {"breakfast_enabled": True}
        result = generate(headers, tid, body)
        if breakfast:
            assert result.status_code == 409, result.text
            assert result.json()["detail"] == "optimizer_breakfast_unavailable"
            assert calls == []
            return
        assert result.status_code == 200, result.text
        assert result.json()["plans"][0]["stats"]["validation_status"] == "PASSED"
        audit = audits(tid)[0]
        rounds = audit.planning_data["routing_rounds"]
        assert len(rounds) in ((2, 3) if repair_needed else (1,))
        assert rounds[-1]["routing_attempts"] == {"point": len(calls)}
        assert len(calls) == sum(len(r["requested_legs"]) for r in rounds)
        if not repair_needed:
            days = result.json()["plans"][0]["days"]
            assert [day["date"] for day in days] == list(dates)
            assert len(calls) == sum(len(day["items"]) + 1 for day in days)
            for day in days:
                assert day["return_travel_minutes"] == 1
                requested = rounds[0]["requested_legs"]
                assert any(leg["origin"] == day["start_point"] for leg in requested)
                assert any(leg["destination"] == day["end_point"] for leg in requested)
        assert audit.optimizer_request["routing_version"] == adapter.ROUTING_VERSION
        assert not audit.optimizer_request["provisional"]
        assert all(e["status"] == "KNOWN" and e["straight_line_m"] is None
                   for e in audit.optimizer_request["edges"])
        assert "synthetic-key" not in str(audit.optimizer_request)
    finally:
        adapter.engine.dispose()


def test_routing_failure_reaches_user_without_provider_details(monkeypatch, planning_adapter):
    from fastapi import FastAPI, HTTPException
    upstream = FastAPI()

    @upstream.post("/snapshot")
    def unavailable():
        raise HTTPException(503, "routing_credentials_rejected")

    monkeypatch.setitem(services.TEST_TRANSPORTS, config.PLANNING_DATA_URL,
                        httpx.ASGITransport(app=upstream))
    headers, tid, _ = ready(individual_method="feature_mean")
    result = generate(headers, tid, configuration())
    assert result.status_code == 503
    assert result.json()["detail"] == "routing_credentials_rejected"
    audit = audits(tid)[0]
    assert audit.error_code == "routing_credentials_rejected"
    assert not audit.optimizer_request
