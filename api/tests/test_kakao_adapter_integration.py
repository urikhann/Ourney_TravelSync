"""Actual Kakao adapter -> CP-SAT -> E3, with synthetic DB/provider evidence."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import config, services
from .test_optimizer_integration import ready, generate, configuration, audits


def test_kakao_adapter_to_optimizer_and_e3(monkeypatch, planning_adapter, tmp_path):
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

    # Exercise the actual HTTP batching/conversion function, replacing only the network client.
    real_client = httpx.AsyncClient

    def respond(req):
        import json
        if req.method == "GET":
            assert req.url.params["avoid"] == "toll"
            return httpx.Response(200, json={"routes": [dict(result_code=0,
                summary=dict(duration=601, distance=500, fare=dict(toll=0)))]})
        payload = json.loads(req.content)
        assert payload["avoid"] == ["toll"]
        assert req.headers["Authorization"] == "KakaoAK synthetic-key"
        return httpx.Response(200, json={"routes": [dict(key=d["key"], result_code=304,
            summary=dict(duration=601, distance=500)) for d in payload["destinations"]]})

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
    monkeypatch.setitem(services.TEST_TRANSPORTS, config.PLANNING_DATA_URL, httpx.ASGITransport(app=adapter.app))
    try:
        headers, tid, _ = ready(individual_method="feature_mean")
        body = configuration()
        body["transport_mode"] = "drive"
        result = generate(headers, tid, body)
        assert result.status_code == 200, result.text
        assert result.json()["plans"][0]["stats"]["validation_status"] == "PASSED"
        audit = audits(tid)[0]
        assert audit.optimizer_request["routing_version"] == adapter.ROUTING_VERSION
        assert all(e["duration_minutes"] == 11 and e["group_fare"] == e["walk_minutes"] == 0
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


def test_multi_destination_quota_falls_back_to_single_routes(monkeypatch, tmp_path):
    """Kakao allows 1,000 multi-destination calls a day but 10,000 single-route calls.
    When the first quota runs out, the same legs are fetched one by one instead of failing the plan."""
    import asyncio
    from fastapi.testclient import TestClient
    monkeypatch.setenv("ROUTE_CACHE_PATH", str(tmp_path / "routes.sqlite3"))
    path = Path(__file__).resolve().parents[2] / "services/planning_data/main.py"
    spec = importlib.util.spec_from_file_location("kakao_quota", path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    calls = {"POST": 0, "GET": 0}

    def respond(req):
        calls[req.method] += 1
        if req.method == "POST":  # Kakao: HTTP 400, code -10 = daily quota used up
            return httpx.Response(400, json={"code": -10, "msg": "quota"})
        return httpx.Response(200, json={"routes": [dict(result_code=0,
            summary=dict(duration=900, distance=4000, fare=dict(toll=0)))]})

    async def no_wait(_):
        return None
    monkeypatch.setattr(adapter, "_retry_pause", no_wait)
    origin = {"latitude": 35.1151, "longitude": 129.0414}
    dests = [(f"p{i}", {"latitude": 35.10 + i / 100, "longitude": 129.05}) for i in range(3)]

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), headers={"Authorization": "KakaoAK k"}) as client:
            return await adapter._fetch_routes_from_origin(client, asyncio.Semaphore(4), "start", origin, dests, "2026-11-06")

    edges = asyncio.run(fetch())
    assert [e["status"] for e in edges] == ["KNOWN"] * 3 and all(e["duration_minutes"] == 15 for e in edges)
    assert calls == {"POST": 3, "GET": 3}  # 1 call + 2 retries, then one single-route call per leg
    health = TestClient(adapter.app).get("/health").json()["kakao_calls_today"]
    assert health["multi_destination_paused"] is True and health["directions"] == 3

    calls.update(POST=0, GET=0)
    asyncio.run(fetch())
    assert calls == {"POST": 0, "GET": 3}  # paused until midnight: no more wasted multi-destination calls
    adapter.engine.dispose()
