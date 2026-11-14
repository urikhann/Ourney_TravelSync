"""Offline provider tests; no real API key, production database or paid requests."""
import asyncio
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("httpx")
pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic")

import httpx
from fastapi.testclient import TestClient
from fastapi import HTTPException

from ourney_travelsync.optimizer.contracts import Edge


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("ROUTE_CACHE_PATH", str(tmp_path / "routes.sqlite3"))
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("KAKAO_REST_API_KEY", "")
    monkeypatch.setenv("PLANNING_DATA_TOKEN", "")
    path = Path(__file__).resolve().parents[1] / "services/planning_data/main.py"
    spec = importlib.util.spec_from_file_location("kakao_planning_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    module.engine.dispose()


def test_batches_conversion_and_directed_failures(adapter):
    calls = []

    def respond(request):
        import json
        body = json.loads(request.content)
        calls.append(body)
        assert body["avoid"] == ["toll"] and body["priority"] == "TIME"
        return httpx.Response(200, json={"routes": [
            {"key": p["key"], "result_code": 0, "summary": {"duration": 61, "distance": 123}}
            for p in body["destinations"]]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await adapter._routes_from_origin(client, asyncio.Semaphore(2), "start",
                {"longitude": 129.1, "latitude": 35.1},
                [(str(i), {"longitude": 129.2, "latitude": 35.2}) for i in range(31)], "2026-11-11")

    edges = asyncio.run(run())
    assert [len(c["destinations"]) for c in calls] == [30, 1]
    assert len(edges) == 31
    for edge in edges:
        parsed = Edge.model_validate(edge)
        assert parsed.usable and parsed.duration_minutes == 2
        assert parsed.distance_m == 123 and parsed.group_fare == parsed.walk_minutes == 0


@pytest.mark.parametrize("body,expected", [
    ({}, "UNKNOWN"),
    ({"routes": [{"key": "p", "result_code": 104}]}, "UNREACHABLE"),
    ({"routes": [{"key": "p", "result_code": 0}]}, "UNKNOWN"),
    ({"routes": [{"key": "p", "result_code": 0, "summary": {"duration": -1, "distance": 1}}]}, "UNKNOWN"),
    ({"routes": [{"key": "p", "result_code": 0, "summary": {"duration": "61", "distance": 1}}]}, "UNKNOWN"),

])
def test_bad_routes_never_invent_times(adapter, body, expected):
    async def run():
        def respond(req):
            return httpx.Response(503) if body is None else httpx.Response(200, json=body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await adapter._routes_from_origin(client, asyncio.Semaphore(1), "s",
                {"longitude": 129, "latitude": 35}, [("p", {"longitude": 129, "latitude": 35})], "2026-11-11")
    edge = Edge.model_validate(asyncio.run(run())[0])
    assert edge.status == expected and not edge.usable
    assert edge.duration_minutes is edge.group_fare is None


def test_auth_mode_and_missing_key(adapter, monkeypatch):
    with TestClient(adapter.app) as client:
        assert client.get("/health").json()["kakao_configured"] is False
        monkeypatch.setattr(adapter, "PLANNING_DATA_TOKEN", "test-only-token")
        assert client.post("/snapshot", json={}).status_code == 401
        headers = {"Authorization": "Bearer test-only-token"}
        assert client.post("/snapshot", headers=headers, json={"schema_version": "planning-data-1.0", "transport_mode": "walk"}).status_code == 422
        monkeypatch.setattr(adapter, "_load_operating", lambda *args: {})
        result = client.post("/snapshot", headers=headers, json={"schema_version": "planning-data-1.0", "transport_mode": "drive", "dataset_version": "test", "context_id": "test"})
        assert result.status_code == 503


@pytest.mark.parametrize("start,end,expected", [
    ("09:00", "18:00", {"start_min": 540, "end_min": 1080}),
    ("22:00", "02:00", {"start_min": 1320, "end_min": 1440}),
    ("09:00", "09:00", None), ("12:99", "18:00", None),
])
def test_opening_window(adapter, start, end, expected):
    assert adapter._opening_window(start, end) == expected


def test_operating_facts_keep_unknown_cost_and_holiday_closure(adapter, monkeypatch):
    from datetime import date, datetime, timezone
    day = date(2026, 11, 11)
    class Rows:
        def __init__(self, values):
            self.values = values
        def mappings(self):
            return self.values
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, statement, params):
            sql = str(statement)
            if "FROM pois" in sql:
                return Rows([dict(id="p", avg_cost_krw=0, duration_min=60,
                    field_status={"cost": "UNKNOWN", "duration": "ESTIMATED", "duration_rule": "fixture-rule",
                                  "hours": "KNOWN", "closed_days": "KNOWN"},
                    closed_on_public_holidays=True, updated_at=datetime.now(timezone.utc))])
            if "FROM public_holidays" in sql:
                return Rows([dict(day=day, name="synthetic holiday")])
            return Rows([])
    class Engine:
        def connect(self):
            return Connection()
        def dispose(self):
            pass
    monkeypatch.setattr(adapter, "engine", Engine())
    result = adapter._load_operating("fixture-version", ["p"], [day])["p"]
    assert result["cost_per_person"]["status"] == "UNKNOWN"
    assert result["cost_per_person"]["value"] is None
    assert result["duration_min"]["rule_version"] == "fixture-rule"
    assert result["operations"][day.isoformat()]["closed"]["value"] is True


@pytest.mark.parametrize("status,code", [
    (401, "routing_credentials_rejected"), (403, "routing_credentials_rejected"),
    (429, "routing_quota_exceeded"), (400, "routing_request_rejected"), (500, "routing_provider_error"),
])
def test_provider_failures_are_not_place_exclusions(adapter, status, code):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: httpx.Response(status, json={"message": "private provider detail"}))) as client:
            return await adapter._routes_from_origin(client, asyncio.Semaphore(1), "s",
                {"longitude": 129, "latitude": 35},
                [("p", {"longitude": 129.1, "latitude": 35.1})], "2026-11-13")
    with pytest.raises(HTTPException) as error:
        asyncio.run(run())
    assert error.value.detail == code
    assert "private" not in str(error.value.detail)


def test_malformed_provider_response_is_service_failure(adapter):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json=[]))) as client:
            await adapter._routes_from_origin(client, asyncio.Semaphore(1), "s",
                {"longitude": 129, "latitude": 35},
                [("p", {"longitude": 129.1, "latitude": 35.1})], "2026-11-13")
    with pytest.raises(HTTPException, match="routing_invalid_response"):
        asyncio.run(run())


@pytest.mark.parametrize("status,code", [("UNKNOWN", "routing_invalid_response"),
                                         ("UNREACHABLE", "routing_no_routes")])
def test_snapshot_with_no_usable_routes_fails_before_optimizer(adapter, monkeypatch, status, code):
    monkeypatch.setattr(adapter, "_load_operating", lambda *args: {})
    async def edges(request):
        return [{"status": status}]
    monkeypatch.setattr(adapter, "_build_drive_edges", edges)
    with TestClient(adapter.app) as client:
        response = client.post("/snapshot", json={"schema_version": "planning-data-1.0",
            "transport_mode": "drive", "dataset_version": "test", "context_id": "test"})
    assert response.status_code == 502
    assert response.json()["detail"] == code


@pytest.mark.parametrize("fallback_code,expected", [(0, "KNOWN"), (103, "UNREACHABLE")])
def test_radius_limit_uses_verified_point_route(adapter, fallback_code, expected):
    calls = []
    def respond(req):
        calls.append(req)
        if req.method == "POST":
            return httpx.Response(200, json={"routes": [
                {"key": "far", "result_code": 304}, {"key": "blocked", "result_code": 103}]})
        assert req.url.params["avoid"] == "toll"
        assert req.url.params["summary"] == "true"
        assert req.url.params["destination"] == "129.3,35.3"
        return httpx.Response(200, json={"routes": [{"result_code": fallback_code,
            "summary": {"duration": 1801, "distance": 21000, "fare": {"toll": 1200}}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await adapter._routes_from_origin(client, asyncio.Semaphore(2), "start",
                {"longitude": 129.1, "latitude": 35.1},
                [("far", {"longitude": 129.3, "latitude": 35.3}),
                 ("blocked", {"longitude": 129.2, "latitude": 35.2})], "2026-11-13")
    edges = [Edge.model_validate(e) for e in asyncio.run(run())]
    assert [r.method for r in calls] == ["POST", "GET"]
    assert edges[0].status == expected
    assert edges[1].status == "UNREACHABLE"
    if fallback_code == 0:
        assert edges[0].duration_minutes == 31
        assert edges[0].group_fare == 1200
        assert edges[0].provider == "KAKAO_MOBILITY_DIRECTIONS_V1"


def test_identical_day_endpoints_share_current_route_fetches(adapter, monkeypatch):
    calls = []
    async def routes(client, sem, origin_id, origin, destinations, day):
        calls.append(origin_id)
        return [{"date": day, "origin": origin_id, "destination": pid,
                 "status": "KNOWN", "duration_minutes": 5} for pid, point in destinations]
    monkeypatch.setattr(adapter, "_routes_from_origin", routes)
    monkeypatch.setattr(adapter, "KAKAO_REST_API_KEY", "synthetic")
    point = {"longitude": 129.1, "latitude": 35.1}
    request = {"pois": [{"poi_id": "p", **point}],
        "points": {k: point for k in ("s1", "s2", "e1", "e2")},
        "days": [{"date": "2026-11-13", "start_point": "s1", "end_point": "e1"},
                 {"date": "2026-11-14", "start_point": "s2", "end_point": "e2"}]}
    edges = asyncio.run(adapter._build_drive_edges(request))
    assert len(calls) == 2
    assert {(e["date"], e["origin"], e["destination"]) for e in edges} == {
        ("2026-11-13", "s1", "p"), ("2026-11-13", "p", "e1"),
        ("2026-11-14", "s2", "p"), ("2026-11-14", "p", "e2")}


def test_route_matrices_only_include_each_days_candidates(adapter, monkeypatch):
    calls = []
    async def routes(client, sem, origin_id, origin, destinations, day):
        calls.append((day, origin_id, [pid for pid, _ in destinations]))
        return []
    monkeypatch.setattr(adapter, "_routes_from_origin", routes)
    monkeypatch.setattr(adapter, "KAKAO_REST_API_KEY", "synthetic")
    point = {"longitude": 129.1, "latitude": 35.1}
    request = {"pois": [{"poi_id": pid, **point} for pid in ("a", "b")],
        "points": {k: point for k in ("s1", "s2", "e1", "e2")},
        "days": [{"date": "2026-11-13", "start_point": "s1", "end_point": "e1"},
                 {"date": "2026-11-14", "start_point": "s2", "end_point": "e2"}],
        "day_candidate_ids": {"2026-11-13": ["a"], "2026-11-14": ["b"]}}
    asyncio.run(adapter._build_drive_edges(request))
    assert calls == [("2026-11-13", "s1", ["a"]), ("2026-11-13", "a", ["e1"]),
                     ("2026-11-14", "s2", ["b"]), ("2026-11-14", "b", ["e2"])]


@pytest.mark.parametrize("status,body", [(500, {"code": -1}), (429, {}), (400, {"code": -10})])
def test_transient_provider_failures_retry_with_bounded_backoff(adapter, monkeypatch, status, body):
    calls, pauses = [], []
    async def pause(seconds):
        pauses.append(seconds)
    monkeypatch.setattr(adapter, "_retry_pause", pause)
    def respond(req):
        calls.append(req)
        if len(calls) < 3:
            return httpx.Response(status, json=body)
        return httpx.Response(200, json={"routes": []})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await adapter._route_request(client, asyncio.Semaphore(1), "POST",
                                                adapter.KAKAO_MULTI_DESTINATION_URL, json={})
    assert asyncio.run(run()).status_code == 200
    assert len(calls) == 3 and pauses == [.5, 1.0]


def test_http_400_quota_is_not_reported_as_bad_parameters(adapter, monkeypatch, caplog):
    calls = []
    async def pause(seconds):
        pass
    monkeypatch.setattr(adapter, "_retry_pause", pause)
    def respond(req):
        calls.append(req)
        return httpx.Response(400, json={"code": -10, "msg": "private data must not be logged"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await adapter._route_request(client, asyncio.Semaphore(1), "GET",
                adapter.KAKAO_DIRECTIONS_URL, params={"origin": "private-coordinates"})
    with pytest.raises(HTTPException, match="routing_quota_exceeded"):
        asyncio.run(run())
    assert len(calls) == 3
    assert "code=-10" in caplog.text and "endpoint=point" in caplog.text
    assert "private" not in caplog.text


def test_invalid_parameters_are_not_retried(adapter):
    calls = []
    def respond(req):
        calls.append(req)
        return httpx.Response(400, json={"code": -2})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await adapter._route_request(client, asyncio.Semaphore(1), "POST",
                                         adapter.KAKAO_MULTI_DESTINATION_URL, json={})
    with pytest.raises(HTTPException, match="routing_request_rejected"):
        asyncio.run(run())
    assert len(calls) == 1


def test_failed_route_group_cancels_pending_requests(adapter):
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def pending():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        async def fail():
            await started.wait()
            raise HTTPException(503, "routing_quota_exceeded")
        with pytest.raises(HTTPException):
            await adapter._gather_routes([pending(), fail()])
        assert cancelled.is_set()
    asyncio.run(run())


@pytest.mark.parametrize("endpoint,code", [("start","routing_start_point_unreachable"),
                                         ("end","routing_end_point_unreachable")])
def test_unreachable_endpoints_are_not_reported_as_all_places_failing(adapter, monkeypatch, endpoint, code):
    monkeypatch.setattr(adapter,"_load_operating",lambda *args:{})
    async def routes(req):
        return [{"date":"2026-11-22","origin":a,"destination":b,
                 "status":"UNREACHABLE" if (a=="start" if endpoint=="start" else b=="end") else "KNOWN"}
                for a,b in [("start","a"),("start","b"),("a","b"),("a","end"),("b","end")]]
    monkeypatch.setattr(adapter,"_build_drive_edges",routes)
    with TestClient(adapter.app) as client:
        response=client.post("/snapshot",json={"schema_version":"planning-data-1.0","transport_mode":"drive",
            "dataset_version":"test","context_id":"test","days":[{"date":"2026-11-22","start_point":"start","end_point":"end"}]})
    assert response.status_code==422
    assert response.json()["detail"]==code


def test_persistent_cache_survives_reload_dates_ids_and_quota(adapter, monkeypatch):
    calls=[]
    def respond(req):
        import json
        body=json.loads(req.content)
        calls.append(body)
        return httpx.Response(200,json={"routes":[{"key":d["key"],"result_code":0,
            "summary":{"duration":61,"distance":123}} for d in body["destinations"]]})
    origin={"longitude":129.1,"latitude":35.1}
    dest={"longitude":129.2,"latitude":35.2}
    async def first():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await adapter._routes_from_origin(client,asyncio.Semaphore(1),"old-start",origin,[("old-poi",dest)],"2026-11-14")
    original=asyncio.run(first())[0]
    # Reload creates a fresh adapter object using the same disk file.
    spec=importlib.util.spec_from_file_location("cache_reload",adapter.__file__)
    restarted=importlib.util.module_from_spec(spec);spec.loader.exec_module(restarted)
    from datetime import datetime,timezone
    monkeypatch.setattr(restarted,"_now",lambda:datetime(2036,1,1,tzinfo=timezone.utc))
    async def cached():
        def offline(req):
            pytest.fail("cached route must not call the provider, even when quota is exhausted")
        async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
            return await restarted._routes_from_origin(client,asyncio.Semaphore(1),"new-start",origin,[("new-poi",dest)],"2036-01-01")
    try:
        edge=asyncio.run(cached())[0]
        assert len(calls)==1 and edge["fetched_at"]==original["fetched_at"]
        assert (edge["date"],edge["origin"],edge["destination"])==("2036-01-01","new-start","new-poi")
        assert edge["duration_minutes"]==2
    finally:restarted.engine.dispose()


def test_cache_keys_are_directed_coordinate_and_policy_specific(adapter,monkeypatch):
    a={"longitude":129.1,"latitude":35.1};b={"longitude":129.2,"latitude":35.2}
    key=adapter._cache_key(a,b)
    assert adapter._cache_key(b,a)!=key
    assert adapter._cache_key(a,{**b,"longitude":129.200001})!=key
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_RADIUS",10000)
    assert adapter._cache_key(a,b)!=key
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_RADIUS",5000)
    monkeypatch.setattr(adapter,"ROUTING_VERSION","changed-policy")
    assert adapter._cache_key(a,b)!=key


def test_concurrent_plans_fetch_identical_legs_once(adapter):
    calls=[]
    async def run():
        async def respond(req):
            import json
            await asyncio.sleep(.01)
            body=json.loads(req.content);calls.append(body)
            return httpx.Response(200,json={"routes":[{"key":d["key"],"result_code":0,
                "summary":{"duration":60,"distance":100}} for d in body["destinations"]]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            a={"longitude":129.1,"latitude":35.1};b={"longitude":129.2,"latitude":35.2}
            return await asyncio.gather(*(adapter._routes_from_origin(client,asyncio.Semaphore(2),"s",a,[("p",b)],"2026-11-14") for _ in range(3)))
    result=asyncio.run(run())
    assert len(calls)==1 and result[0]==result[1]==result[2]


def test_failures_are_not_cached_and_partial_pool_only_fetches_misses(adapter):
    calls=[]
    def respond(req):
        import json
        body=json.loads(req.content);calls.append([d["key"] for d in body["destinations"]])
        return httpx.Response(200,json={"routes":[{"key":d["key"],"result_code":103} if d["key"]=="bad" else
            {"key":d["key"],"result_code":0,"summary":{"duration":60,"distance":100}} for d in body["destinations"]]})
    async def run():
        a={"longitude":129.1,"latitude":35.1};b={"longitude":129.2,"latitude":35.2};c={"longitude":129.3,"latitude":35.3}
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for _ in range(2):
                await adapter._routes_from_origin(client,asyncio.Semaphore(1),"s",a,[("good",b),("bad",c)],"2026-11-14")
    asyncio.run(run())
    assert calls==[["good","bad"],["bad"]]


def test_cache_failure_blocks_provider_calls(adapter,monkeypatch,tmp_path):
    blocker=tmp_path/"file";blocker.write_text("not a directory")
    monkeypatch.setattr(adapter,"ROUTE_CACHE_PATH",str(blocker/"routes.sqlite3"))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:pytest.fail("must not call Kakao"))) as client:
            await adapter._routes_from_origin(client,asyncio.Semaphore(1),"s",{"longitude":129,"latitude":35},[("p",{"longitude":129.1,"latitude":35.1})],"2026-11-14")
    with pytest.raises(HTTPException,match="routing_cache_unavailable"):asyncio.run(run())


def test_seed_imports_saved_successful_edges_without_network(adapter,monkeypatch,capsys):
    import json,sys
    from sqlalchemy import text
    origin={"longitude":129.1,"latitude":35.1};dest={"longitude":129.2,"latitude":35.2}
    edge=dict(date="2026-11-14",origin="s",destination="p",status="KNOWN",duration_minutes=5,
        distance_m=100,group_fare=0,walk_minutes=0,provider=adapter.PROVIDER,fetched_at="2026-11-14T00:00:00+00:00")
    data={"request":{"pois":[{"poi_id":"p",**dest}],"points":{"s":origin}},
          "response":{"routing_version":adapter.ROUTING_VERSION,"edges":[edge,{**edge,"status":"UNKNOWN"}]}}
    with adapter.engine.begin() as c:
        c.execute(text("CREATE TABLE itinerary_optimization_runs (id INTEGER, planning_data TEXT)"))
        c.execute(text("INSERT INTO itinerary_optimization_runs VALUES (1,:data)"),{"data":json.dumps(data)})
    monkeypatch.setitem(sys.modules,"main",adapter)
    spec=importlib.util.spec_from_file_location("cache_seed",Path(adapter.__file__).with_name("seed_route_cache.py"))
    seed=importlib.util.module_from_spec(spec);spec.loader.exec_module(seed)
    seed.seed()
    stored=adapter._cached_routes([adapter._cache_key(origin,dest)])
    assert len(stored)==1 and next(iter(stored.values()))["fetched_at"]==edge["fetched_at"]
    assert '"provider_requests": 0' in capsys.readouterr().out
