"""Endpoint quotas and bounded expansion; all HTTP responses are synthetic."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("fastapi")
from fastapi import HTTPException

from test_kakao_planning_data import adapter  # shared isolated cache/DB fixture


def test_batch_counts_once_for_thirty_destinations_and_cache_hits_cost_zero(adapter):
    calls=[]
    def respond(req):
        calls.append(req)
        return httpx.Response(200,json={"routes":[dict(key=d["key"],result_code=0,
            summary=dict(duration=60,distance=500)) for d in json.loads(req.content)["destinations"]]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            origin=dict(latitude=35.1,longitude=129.1)
            targets=[(str(i),dict(latitude=35.2,longitude=129.2+i*.001)) for i in range(30)]
            await adapter._routes_from_origin(client,asyncio.Semaphore(1),"s",origin,targets,"2026-11-23")
            await adapter._routes_from_origin(client,asyncio.Semaphore(1),"s",origin,targets,"2026-11-24")
    asyncio.run(run())
    status=adapter._budget_status()["endpoints"]
    assert len(calls)==1 and status["batch"]["local_attempts"]==1
    assert status["point"]["local_attempts"]==0
    assert status["batch"]["usable_limit"]==900 and status["point"]["usable_limit"]==9000


def test_provider_cooldown_only_blocks_the_endpoint_that_failed(adapter):
    calls=[]
    def respond(req):
        calls.append(req)
        return httpx.Response(429,headers={"Retry-After":"60"}) if req.method=="POST" else httpx.Response(200,json={})
    async def run():
        for _ in range(2):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                with pytest.raises(HTTPException,match="routing_quota_exceeded"):
                    await adapter._route_request(client,asyncio.Semaphore(1),"POST",adapter.KAKAO_MULTI_DESTINATION_URL)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await adapter._route_request(client,asyncio.Semaphore(1),"GET",adapter.KAKAO_DIRECTIONS_URL)
    asyncio.run(run())
    status=adapter._budget_status()["endpoints"]
    assert len(calls)==2
    assert status["batch"]["cooldown_seconds"]>0 and status["point"]["cooldown_seconds"]==0
    assert status["batch"]["local_attempts"]==status["point"]["local_attempts"]==1


def test_batch_allowance_exhaustion_can_use_independent_point_allowance(adapter,monkeypatch):
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_BATCH_DAILY_QUOTA",0)
    calls=[]
    def respond(req):
        calls.append(req)
        assert req.method=="GET"
        return httpx.Response(200,json={"routes":[dict(result_code=0,
            summary=dict(duration=60,distance=500,fare=dict(toll=0)))]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await adapter._routes_from_origin(client,asyncio.Semaphore(1),"s",
                dict(latitude=35.1,longitude=129.1),[("p",dict(latitude=35.2,longitude=129.2))],"2026-11-23")
    edges=asyncio.run(run())
    assert len(calls)==1 and edges[0]["status"]=="KNOWN"
    status=adapter._budget_status()["endpoints"]
    assert status["batch"]["local_attempts"]==0 and status["point"]["local_attempts"]==1


def test_plan_endpoint_share_does_not_block_other_endpoint(adapter,monkeypatch):
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_BATCH_DAILY_QUOTA",10)
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_QUOTA_RESERVE_PERCENT",0)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={}))) as client:
            semaphore=asyncio.Semaphore(1)
            await adapter._route_request(client,semaphore,"POST",adapter.KAKAO_MULTI_DESTINATION_URL)
            with pytest.raises(HTTPException,match="routing_request_budget_exceeded"):
                await adapter._route_request(client,semaphore,"POST",adapter.KAKAO_MULTI_DESTINATION_URL)
            await adapter._route_request(client,semaphore,"GET",adapter.KAKAO_DIRECTIONS_URL)
    asyncio.run(run())
    assert adapter._budget_status()["endpoints"]["batch"]["remaining"]==9


def test_daily_counter_is_atomic_across_workers(adapter,monkeypatch):
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_BATCH_DAILY_QUOTA",7)
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_QUOTA_RESERVE_PERCENT",0)
    def reserve(_):
        try:
            adapter._reserve_request("batch")
            return True
        except HTTPException as exc:
            assert exc.detail=="routing_daily_budget_exceeded"
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(reserve,range(25)))
    assert sum(results)==7
    assert adapter._budget_status()["endpoints"]["batch"]["remaining"]==0
    assert adapter._budget_status()["endpoints"]["point"]["local_attempts"]==0


def test_kst_midnight_resets_daily_usage(adapter,monkeypatch):
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_BATCH_DAILY_QUOTA",1)
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_QUOTA_RESERVE_PERCENT",0)
    monkeypatch.setattr(adapter,"_now",lambda:datetime(2026,11,22,14,59,tzinfo=timezone.utc))
    adapter._reserve_request("batch")
    with pytest.raises(HTTPException,match="routing_daily_budget_exceeded"): adapter._reserve_request("batch")
    monkeypatch.setattr(adapter,"_now",lambda:datetime(2026,11,22,15,1,tzinfo=timezone.utc))
    adapter._reserve_request("batch")
    assert adapter._budget_status()["day_kst"]=="2026-11-23"
    assert adapter._budget_status()["endpoints"]["batch"]["local_attempts"]==1


def test_existing_combined_usage_migrates_conservatively_without_double_counting(adapter):
    scope=hashlib.sha256(adapter.KAKAO_REST_API_KEY.encode()).hexdigest()
    day=adapter._now().astimezone(adapter.ZoneInfo("Asia/Seoul")).date().isoformat()
    with adapter._cache_connection() as c:
        c.execute("CREATE TABLE request_budget(scope TEXT PRIMARY KEY,day TEXT,used INTEGER,blocked_until REAL)")
        c.execute("INSERT INTO request_budget VALUES (?,?,12,0)",(scope,day))
    adapter._reserve_request("batch")
    adapter._reserve_request("point")
    assert {v["local_attempts"] for v in adapter._budget_status()["endpoints"].values()}=={13}
    adapter._reserve_request("batch")
    assert adapter._budget_status()["endpoints"]["batch"]["local_attempts"]==14
    assert adapter._budget_status()["endpoints"]["point"]["local_attempts"]==13


def test_retry_after_http_date_and_failed_attempts_are_accounted(adapter,monkeypatch):
    from email.utils import format_datetime
    now=datetime(2026,11,23,tzinfo=timezone.utc)
    monkeypatch.setattr(adapter,"_now",lambda:now)
    adapter._reserve_request("point")
    adapter._block_requests("point",format_datetime(now+timedelta(seconds=120),usegmt=True))
    assert adapter._budget_status()["endpoints"]["point"]["cooldown_seconds"]==120
    assert adapter._budget_status()["endpoints"]["batch"]["cooldown_seconds"]==0


def graph_request(count=1):
    return dict(pois=[dict(poi_id=str(i),latitude=35.11+i*.01,longitude=129.11+i*.01) for i in range(count)],
        points=dict(s=dict(latitude=35.1,longitude=129.1),e=dict(latitude=35.2,longitude=129.2)),
        days=[dict(date="2026-11-23",start_point="s",end_point="e")])


def test_http_retries_charge_the_correct_endpoint(adapter,monkeypatch):
    calls=[]
    async def pause(_): pass
    monkeypatch.setattr(adapter,"_retry_pause",pause)
    def respond(req):
        calls.append(req)
        return httpx.Response(500 if len(calls)<3 else 200,json={})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await adapter._route_request(client,asyncio.Semaphore(1),"POST",adapter.KAKAO_MULTI_DESTINATION_URL)
    asyncio.run(run())
    status=adapter._budget_status()["endpoints"]
    assert len(calls)==status["batch"]["local_attempts"]==3
    assert status["point"]["local_attempts"]==0


def fake_batches(adapter,monkeypatch):
    monkeypatch.setattr(adapter,"KAKAO_REST_API_KEY","test-only")
    calls=[]
    real_client=httpx.AsyncClient
    def respond(req):
        calls.append(req)
        return httpx.Response(200,json={"routes":[dict(key=d["key"],result_code=0,
            summary=dict(duration=60,distance=500)) for d in json.loads(req.content)["destinations"]]})
    monkeypatch.setattr(adapter.httpx,"AsyncClient",lambda **kw:real_client(transport=httpx.MockTransport(respond),**kw))
    return calls


def test_expansion_reuses_cache_and_one_shared_hard_cap(adapter,monkeypatch):
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_INITIAL_REQUESTS",1)
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_MAX_REQUESTS",4)
    calls=fake_batches(adapter,monkeypatch)
    edges=asyncio.run(adapter._build_drive_edges(graph_request()))
    assert len(calls)==2 and all(e["status"]=="KNOWN" for e in edges)
    assert not edges.warnings
    assert adapter._budget_status()["endpoints"]["batch"]["local_attempts"]==2


def test_expansion_never_exceeds_total_cap(adapter,monkeypatch):
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_INITIAL_REQUESTS",1)
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_MAX_REQUESTS",2)
    calls=fake_batches(adapter,monkeypatch)
    edges=asyncio.run(adapter._build_drive_edges(graph_request(2)))
    assert len(calls)==2 and any(e["status"]=="UNKNOWN" for e in edges)
    assert edges.warnings==("routing_request_budget_exceeded",)


def test_connected_graph_does_not_expand_just_to_fill_unused_edges(adapter,monkeypatch):
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_INITIAL_REQUESTS",1)
    monkeypatch.setattr(adapter,"KAKAO_ROUTE_MAX_REQUESTS",10)
    calls=fake_batches(adapter,monkeypatch)
    req=graph_request(2)
    known=dict(status="KNOWN",duration_minutes=1,distance_m=500,group_fare=0,walk_minutes=0,
               provider=adapter.PROVIDER,fetched_at=adapter._now().isoformat())
    adapter._save_routes([(adapter._cache_key(a,b),known) for p in req["pois"]
                          for a,b in ((req["points"]["s"],p),(p,req["points"]["e"]))])
    edges=asyncio.run(adapter._build_drive_edges(req))
    assert len(calls)==1 and any(e["status"]=="UNKNOWN" for e in edges)
    assert not adapter._needs_route_expansion(req,edges)


def test_selected_leg_falls_back_to_batch_without_resetting_plan_allowance(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "KAKAO_ROUTE_INITIAL_REQUESTS", 1)
    monkeypatch.setattr(adapter, "KAKAO_ROUTE_MAX_REQUESTS", 2)
    monkeypatch.setattr(adapter, "KAKAO_ROUTE_POINT_DAILY_QUOTA", 0)
    calls = fake_batches(adapter, monkeypatch)
    req = graph_request(2)
    day = req["days"][0]["date"]
    req["day_candidate_ids"] = {day: ["0", "1"]}
    first = dict(date=day, origin="s", destination="0")
    second = dict(date=day, origin="0", destination="1")
    third = dict(date=day, origin="1", destination="e")
    edges, spent = asyncio.run(adapter._build_selected_edges({**req, "route_legs": [first]}))
    assert edges[0]["status"] == "KNOWN" and spent == {"batch": 1}
    edges, spent = asyncio.run(adapter._build_selected_edges(
        {**req, "route_legs": [first, second, third], "routing_attempts": spent, "expand_routing_budget": True}))
    assert len(calls) == 2 and spent == {"batch": 2}
    assert sum(edge["status"] == "KNOWN" for edge in edges) == 2
    assert edges.warnings == ("routing_request_budget_exceeded",)
