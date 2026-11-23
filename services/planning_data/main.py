"""TravelSync planning-data adapter using Kakao Mobility car routing.

This service fills the existing planning-data-1.0 boundary. It:
- reads already-imported dwell/cost/opening-hour facts from the TravelSync DB,
- obtains directed driving duration/distance from Kakao Mobility,
- emits route edges for the OR-Tools optimizer.

Unknown cost/hours remain UNKNOWN; no synthetic values are invented.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import logging
import os
import sqlite3
import weakref
from pathlib import Path
from contextlib import contextmanager
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException
from sqlalchemy import bindparam, create_engine, text
try:
    from ourney_travelsync.operating_schedules import load_normalized, day_windows, load_versioned_weekly, load_versioned_dated, dataset_diagnostics
except ModuleNotFoundError:  # standalone planning-data image
    from operating_schedules import load_normalized, day_windows, load_versioned_weekly, load_versioned_dated, dataset_diagnostics

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://travelsync:travelsync@db:5432/travelsync",
)
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
PLANNING_DATA_TOKEN = os.getenv("PLANNING_DATA_TOKEN", "")
KAKAO_ROUTE_RADIUS = min(10000, max(1, int(os.getenv("KAKAO_ROUTE_RADIUS", "5000"))))
KAKAO_ROUTE_CONCURRENCY = max(1, int(os.getenv("KAKAO_ROUTE_CONCURRENCY", "2")))
KAKAO_ROUTE_INITIAL_REQUESTS = max(1, int(os.getenv("KAKAO_ROUTE_INITIAL_REQUESTS", "80")))
KAKAO_ROUTE_MAX_REQUESTS = max(1, int(os.getenv("KAKAO_ROUTE_MAX_REQUESTS", "200")))
KAKAO_ROUTE_BATCH_DAILY_QUOTA = max(0, int(os.getenv("KAKAO_ROUTE_BATCH_DAILY_QUOTA", "1000")))
KAKAO_ROUTE_POINT_DAILY_QUOTA = max(0, int(os.getenv("KAKAO_ROUTE_POINT_DAILY_QUOTA", "10000")))
KAKAO_ROUTE_QUOTA_RESERVE_PERCENT = min(100, max(0, int(os.getenv("KAKAO_ROUTE_QUOTA_RESERVE_PERCENT", "10"))))
KAKAO_ROUTE_PLAN_QUOTA_SHARE_PERCENT = min(100, max(1, int(os.getenv("KAKAO_ROUTE_PLAN_QUOTA_SHARE_PERCENT", "10"))))

ROUTE_CACHE_PATH = os.getenv("ROUTE_CACHE_PATH", "/cache/routes.sqlite3")
_origin_locks = weakref.WeakKeyDictionary()

KAKAO_MULTI_DESTINATION_URL = "https://apis-navi.kakaomobility.com/v1/destinations/directions"
KAKAO_DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"
logger = logging.getLogger(__name__)
PROVIDER = "KAKAO_MOBILITY_MULTI_DESTINATION_V1_TOLL_FREE"
ROUTING_VERSION = "kakao-drive-multidestination-with-point-fallback-v2"
ROUTING_LIMITS = {"routing_quota_exceeded", "routing_daily_budget_exceeded", "routing_request_budget_exceeded"}

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
app = FastAPI(title="TravelSync planning data", version="1.0")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return _now().isoformat()
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _hash(value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _fact(value=None, *, status="UNKNOWN", source=None, verified_at=None,
          rule_version=None, user_upper_bound=False):
    return {
        "value": value,
        "status": status,
        "source": source,
        "verified_at": verified_at,
        "rule_version": rule_version,
        "user_upper_bound": user_upper_bound,
    }


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m != 0):
        raise ValueError("invalid clock time")
    return h * 60 + m


def _opening_window(open_time: str, close_time: str):
    try:
        start = _minutes(open_time)
        end = _minutes(close_time)
    except (AttributeError, TypeError, ValueError):
        return None
    if end == start:
        return None  # ambiguous: do not invent all-day opening
    if end < start:
        end = 1440
    if not (0 <= start < end <= 1440):
        return None
    return {"start_min": start, "end_min": end}


def _auth(authorization: str | None):
    if PLANNING_DATA_TOKEN and authorization != f"Bearer {PLANNING_DATA_TOKEN}":
        raise HTTPException(401, "planning_data_unauthorized")


def _load_operating(dataset_version: str, poi_ids: list[str], days: list[date]) -> dict:
    if not poi_ids:
        return {}

    poi_sql = text("""
        SELECT id, avg_cost_krw, duration_min, field_status,
               closed_on_public_holidays, updated_at
        FROM pois
        WHERE dataset_version = :version AND id IN :ids
    """).bindparams(bindparam("ids", expanding=True))
    hours_sql = text(
        "SELECT poi_id, dow, open, close FROM poi_hours WHERE poi_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    exc_sql = text("""
        SELECT poi_id, day, closed, open, close, reason
        FROM poi_exceptions
        WHERE poi_id IN :ids AND day IN :days
    """).bindparams(bindparam("ids", expanding=True), bindparam("days", expanding=True))
    holiday_sql = text(
        "SELECT day, name FROM public_holidays WHERE day IN :days"
    ).bindparams(bindparam("days", expanding=True))

    with engine.connect() as c:
        normalized = load_normalized(c, dataset_version, poi_ids)
        pois = {r["id"]: dict(r) for r in c.execute(
            poi_sql, {"version": dataset_version, "ids": poi_ids}
        ).mappings()}
        frozen = load_versioned_weekly(c, dataset_version, poi_ids)
        hour_rows = ([r for rows in frozen.values() for r in rows] if frozen is not None
                     else [dict(r) for r in c.execute(hours_sql, {"ids": poi_ids}).mappings()])
        baseline_dated = load_versioned_dated(c, dataset_version, poi_ids)
        exc_rows = [dict(r) for r in c.execute(
            exc_sql, {"ids": poi_ids, "days": days}
        ).mappings()] if days else []
        holiday_rows = [dict(r) for r in c.execute(
            holiday_sql, {"days": days}
        ).mappings()] if days else []

    hours: dict[str, dict[int, list[dict]]] = {}
    for r in hour_rows:
        hours.setdefault(r["poi_id"], {}).setdefault(r["dow"], []).append(r)
    exceptions = {(r["poi_id"], date.fromisoformat(r["day"]) if isinstance(r["day"],str) else r["day"]): r for r in baseline_dated + exc_rows}
    holidays = {date.fromisoformat(r["day"]) if isinstance(r["day"],str) else r["day"]: r["name"] for r in holiday_rows}

    result = {}
    for pid in poi_ids:
        row = pois.get(pid)
        if row is None:
            continue
        status = row["field_status"] or {}
        if isinstance(status, str):
            try:
                status = json.loads(status)
            except json.JSONDecodeError:
                status = {}

        source = f"travelsync-db:{dataset_version}"
        verified = _iso(row.get("updated_at"))

        duration_status = status.get("duration", "UNKNOWN")
        if row.get("duration_min") is not None and duration_status in ("KNOWN", "ESTIMATED"):
            duration = _fact(
                int(row["duration_min"]), status=duration_status, source=source,
                verified_at=verified,
                rule_version=status.get("duration_rule") if duration_status == "ESTIMATED" else None,
            )
        else:
            duration = _fact()

        if row.get("avg_cost_krw") is not None and status.get("cost") == "KNOWN":
            cost = _fact(int(row["avg_cost_krw"]), status="KNOWN", source=source,
                         verified_at=verified)
        else:
            cost = _fact()

        operations = {}
        for d in days:
            exc = exceptions.get((pid, d))
            known_hours = status.get("hours") == "KNOWN"
            known_closed_days = status.get("closed_days") == "KNOWN"
            opening_fact = _fact()
            closed_fact = _fact()
            event_fact = _fact()

            if exc is not None:
                if exc["closed"]:
                    opening_fact = _fact([], status="KNOWN", source=source, verified_at=verified)
                    closed_fact = _fact(True, status="KNOWN", source=source, verified_at=verified)
                elif exc.get("open") and exc.get("close"):
                    w = _opening_window(exc["open"], exc["close"])
                    if w:
                        opening_fact = _fact([w], status="KNOWN", source=source, verified_at=verified)
                        closed_fact = _fact(False, status="KNOWN", source=source, verified_at=verified)
            elif d in holidays and row.get("closed_on_public_holidays") and known_closed_days:
                opening_fact = _fact([], status="KNOWN", source=source, verified_at=verified)
                closed_fact = _fact(True, status="KNOWN", source=source, verified_at=verified)
            elif pid in normalized and day_windows(normalized[pid], d) is not None:
                record = normalized[pid]
                normalized_windows = day_windows(record, d)
                source_ref = f"normalized-hours:{record['batch_id']}:{pid}"
                opening_fact = _fact([{"start_min": start, "end_min": end} for start, end in normalized_windows],
                                     status="KNOWN", source=source_ref, verified_at=record["observed_at"])
                closed_fact = _fact(not bool(normalized_windows), status="KNOWN", source=source_ref,
                                    verified_at=record["observed_at"])
            elif known_hours:
                dow = (d.weekday() + 1) % 7  # project: Sunday=0
                rows = hours.get(pid, {}).get(dow, [])
                windows = [w for w in (_opening_window(r["open"], r["close"]) for r in rows) if w]
                if windows:
                    opening_fact = _fact(windows, status="KNOWN", source=source, verified_at=verified)
                    closed_fact = _fact(False, status="KNOWN", source=source, verified_at=verified)
                elif hours.get(pid):
                    opening_fact = _fact([], status="KNOWN", source=source, verified_at=verified)
                    closed_fact = _fact(True, status="KNOWN", source=source, verified_at=verified)

            operations[d.isoformat()] = {
                "openings": opening_fact,
                "closed": closed_fact,
                "event_active": event_fact,
            }

        result[pid] = {
            "poi_id": pid,
            "duration_min": duration,
            "cost_per_person": cost,
            "operations": operations,
            "access": {},
        }
    return result


def _chunk(items, size=30):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _provider_error_code(response):
    try:
        value = response.json().get("code")
        return value if type(value) is int else None
    except (ValueError, AttributeError):
        return None


def _check_response(response):
    if response.is_success:
        return
    code = _provider_error_code(response)
    # Log only fixed endpoint names and a numeric provider code. Provider messages,
    # request URLs, headers, coordinates, and credentials never enter the logs.
    endpoint = "point" if response.request.url.path == "/v1/directions" else "multi-destination"
    logger.warning("Kakao routing HTTP failure: endpoint=%s status=%s code=%s",
                   endpoint, response.status_code, code)
    if response.status_code in (401, 403) or code == -401:
        raise HTTPException(503, "routing_credentials_rejected")
    # Kakao also documents quota exhaustion as HTTP 400 / code -10.
    if response.status_code == 429 or code == -10:
        raise HTTPException(503, "routing_quota_exceeded")
    if 400 <= response.status_code < 500:
        raise HTTPException(502, "routing_request_rejected")
    raise HTTPException(502, "routing_provider_error")


async def _retry_pause(seconds):
    await asyncio.sleep(seconds)


async def _route_request(client, semaphore, method, url, **kwargs):
    endpoint = "point" if url == KAKAO_DIRECTIONS_URL else "batch"
    if not hasattr(client, "_travelsync_reservation_lock"):
        client._travelsync_reservation_lock = asyncio.Lock()
    for attempt in range(3):
        async with semaphore:
            # A rejected daily reservation must never occupy the last plan slot
            # while another endpoint still has room to serve it.
            async with client._travelsync_reservation_lock:
                limit = min(getattr(client, "_travelsync_plan_limit", KAKAO_ROUTE_INITIAL_REQUESTS), KAKAO_ROUTE_MAX_REQUESTS)
                total = getattr(client, "_travelsync_route_requests", 0)
                counts = getattr(client, "_travelsync_endpoint_requests", {})
                share = max(1, _daily_limit(endpoint) * KAKAO_ROUTE_PLAN_QUOTA_SHARE_PERCENT // 100)
                if total >= limit or counts.get(endpoint, 0) >= share:
                    raise HTTPException(503, "routing_request_budget_exceeded")
                await asyncio.to_thread(_reserve_request, endpoint)
                client._travelsync_route_requests = total + 1
                counts[endpoint] = counts.get(endpoint, 0) + 1
                client._travelsync_endpoint_requests = counts
            response = await client.request(method, url, **kwargs)
            if response.status_code == 429 or _provider_error_code(response) == -10:
                await asyncio.to_thread(_block_requests, endpoint, response.headers.get("Retry-After"))
                _check_response(response)
        transient = response.status_code in (500, 502, 503, 504)
        if transient and attempt < 2:
            await _retry_pause(.5 * (2 ** attempt))
            continue
        _check_response(response)
        return response


async def _gather_routes(tasks):
    pending = [asyncio.ensure_future(task) for task in tasks]
    try:
        return await asyncio.gather(*pending)
    except BaseException:
        # gather alone leaves sibling requests running after the snapshot fails.
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise


async def _point_route(client, semaphore, origin, destination):
    try:
        response = await _route_request(client, semaphore, "GET", KAKAO_DIRECTIONS_URL, params={
                "origin": f"{origin['longitude']},{origin['latitude']}",
                "destination": f"{destination['longitude']},{destination['latitude']}",
                "priority": "TIME", "avoid": "toll", "summary": "true",
                "alternatives": "false",
            })
        routes = response.json()["routes"]
        route = routes[0]
        if not isinstance(route, dict):
            raise ValueError("invalid route")
        return {**route, "_point_route": True}
    except httpx.HTTPError:
        raise HTTPException(503, "routing_provider_unavailable") from None
    except (ValueError, AttributeError, TypeError, KeyError, IndexError):
        raise HTTPException(502, "routing_invalid_response") from None


def _cache_key(origin, destination):
    # Exact directed coordinates and every routing-policy input. No trip/date/POI
    # IDs: the provider observes current traffic, not a future departure date.
    return _hash({"version": ROUTING_VERSION, "mode": "drive", "priority": "TIME",
                  "avoid": ["toll"], "radius": KAKAO_ROUTE_RADIUS,
                  "origin": [float(origin[k]).hex() for k in ("longitude", "latitude")],
                  "destination": [float(destination[k]).hex() for k in ("longitude", "latitude")]})


@contextmanager
def _cache_connection():
    path = Path(ROUTE_CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS routes (key TEXT PRIMARY KEY, data TEXT NOT NULL)")
            yield connection
    finally:
        connection.close()


def _daily_limit(endpoint):
    quota = KAKAO_ROUTE_POINT_DAILY_QUOTA if endpoint == "point" else KAKAO_ROUTE_BATCH_DAILY_QUOTA
    return quota * (100 - KAKAO_ROUTE_QUOTA_RESERVE_PERCENT) // 100


def _quota_table(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS endpoint_request_budget (
        scope TEXT NOT NULL, endpoint TEXT NOT NULL, day TEXT NOT NULL,
        used INTEGER NOT NULL, blocked_until REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(scope,endpoint))""")


def _budget_row(connection, scope, endpoint, day):
    row = connection.execute("SELECT day,used,blocked_until FROM endpoint_request_budget WHERE scope=? AND endpoint=?", (scope,endpoint)).fetchone()
    if row:
        return row
    # Old releases tracked a combined count. Its endpoint split is unknowable:
    # conservatively carry it to each bucket for the current KST date only.
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='request_budget'").fetchone():
        old = connection.execute("SELECT day,used,blocked_until FROM request_budget WHERE scope=?", (scope,)).fetchone()
        if old:
            return (day, old[1] if old[0] == day else 0, old[2])
    return (day, 0, 0)


def _reserve_request(endpoint):
    """Atomic per-endpoint HTTP-attempt accounting, shared across workers.

    One batch HTTP attempt counts once regardless of destination count. Retries
    and point fallbacks use their own endpoint's allowance. Cache hits cost zero.
    """
    scope = hashlib.sha256(KAKAO_REST_API_KEY.encode()).hexdigest()
    now = _now()
    day = now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    try:
        with _cache_connection() as connection:
            _quota_table(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = _budget_row(connection, scope, endpoint, day)
            if row[2] > now.timestamp():
                raise HTTPException(503, "routing_quota_exceeded")
            used = row[1] if row[0] == day else 0
            if used >= _daily_limit(endpoint):
                raise HTTPException(503, "routing_daily_budget_exceeded")
            connection.execute("INSERT INTO endpoint_request_budget VALUES (?,?,?,?,0) ON CONFLICT(scope,endpoint) DO UPDATE SET day=excluded.day,used=excluded.used",
                               (scope,endpoint,day,used+1))
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "routing_cache_unavailable") from None


def _block_requests(endpoint, retry_after):
    try:
        delay = float(retry_after)
    except (ValueError, TypeError):
        try:
            delay = (parsedate_to_datetime(retry_after) - _now()).total_seconds()
        except (ValueError, TypeError, OverflowError):
            delay = 3600  # Unknown provider reset: do not guess quota renewal.
    delay = max(1, min(86400, delay))
    scope = hashlib.sha256(KAKAO_REST_API_KEY.encode()).hexdigest()
    try:
        with _cache_connection() as connection:
            _quota_table(connection)
            connection.execute("UPDATE endpoint_request_budget SET blocked_until=max(blocked_until,?) WHERE scope=? AND endpoint=?", (_now().timestamp()+delay,scope,endpoint))
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "routing_cache_unavailable") from None


def _budget_status():
    now = _now()
    day = now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    scope = hashlib.sha256(KAKAO_REST_API_KEY.encode()).hexdigest()
    try:
        with _cache_connection() as connection:
            _quota_table(connection)
            result = {}
            for endpoint,quota in (("batch",KAKAO_ROUTE_BATCH_DAILY_QUOTA),("point",KAKAO_ROUTE_POINT_DAILY_QUOTA)):
                row = _budget_row(connection, scope, endpoint, day)
                used = row[1] if row[0] == day else 0
                result[endpoint] = dict(configured_quota=quota,usable_limit=_daily_limit(endpoint),
                    local_attempts=used,remaining=max(0,_daily_limit(endpoint)-used),
                    cooldown_seconds=max(0,math.ceil(row[2]-now.timestamp())))
            return dict(day_kst=day, reserve_percent=KAKAO_ROUTE_QUOTA_RESERVE_PERCENT, endpoints=result)
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "routing_cache_unavailable") from None


def _valid_cached_route(edge):
    try:
        stamp = datetime.fromisoformat(edge["fetched_at"].replace("Z", "+00:00"))
        return (edge["status"] == "KNOWN" and edge["provider"] in
                (PROVIDER, "KAKAO_MOBILITY_DIRECTIONS_V1") and stamp.tzinfo is not None
                and all(type(edge[k]) is int and 0 <= edge[k] <= upper for k,upper in
                    (("duration_minutes",1440),("distance_m",10**9),("group_fare",10**9),("walk_minutes",1440))))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


_CACHE_FIELDS = ("status", "duration_minutes", "distance_m", "group_fare", "walk_minutes", "provider", "fetched_at")


def _cached_routes(keys):
    try:
        with _cache_connection() as connection:
            found = {}
            for key in set(keys):
                row = connection.execute("SELECT data FROM routes WHERE key=?", (key,)).fetchone()
                if row:
                    try:
                        edge = json.loads(row[0])
                    except (ValueError, TypeError):
                        continue
                    if _valid_cached_route(edge):
                        found[key] = {k: edge[k] for k in _CACHE_FIELDS}
            return found
    except (OSError, sqlite3.Error):
        # A broken cache must not silently spend the remaining provider quota.
        raise HTTPException(503, "routing_cache_unavailable") from None


def _save_routes(entries):
    try:
        with _cache_connection() as connection:
            for key, edge in entries:
                if _valid_cached_route(edge):
                    value = {k: edge[k] for k in _CACHE_FIELDS}
                    connection.execute("INSERT INTO routes(key,data) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data WHERE json_extract(excluded.data,'$.fetched_at') > json_extract(routes.data,'$.fetched_at')",
                                       (key, json.dumps(value)))
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "routing_cache_unavailable") from None


async def _routes_from_origin(client, semaphore, origin_id, origin, destinations, day):
    # Serialize identical origins across simultaneous snapshots. Recheck disk
    # after acquiring the lock so shared legs are fetched only once per process.
    locks = _origin_locks.setdefault(asyncio.get_running_loop(), {})
    origin_key = _cache_key(origin, origin)
    async with locks.setdefault(origin_key, asyncio.Lock()):
        keys = {pid: _cache_key(origin, point) for pid,point in destinations}
        cached = await asyncio.to_thread(_cached_routes, keys.values())
        misses = [(pid,point) for pid,point in destinations if keys[pid] not in cached]
        logger.info("Demo route cache: hits=%s misses=%s", len(destinations)-len(misses), len(misses))
        if misses:
            if not KAKAO_REST_API_KEY and client.headers.get("Authorization", "").strip() == "KakaoAK":
                raise HTTPException(503, "kakao_rest_api_key_not_configured")
            try:
                fresh = await _fetch_routes_from_origin(client, semaphore, origin_id, origin, misses, day)
            except HTTPException as exc:
                if exc.detail not in ROUTING_LIMITS:
                    raise
                client._travelsync_limit_reason = exc.detail
                # Successful batches/fallbacks may have completed before the limit.
                cached = await asyncio.to_thread(_cached_routes, keys.values())
                fresh = []
        else:
            fresh = []
        by_id = {e["destination"]: e for e in fresh}
        def edge(pid):
            if keys[pid] in cached:
                return {**cached[keys[pid]], "date": day, "origin": origin_id, "destination": pid}
            return by_id.get(pid) or dict(date=day, origin=origin_id, destination=pid,
                status="UNKNOWN", duration_minutes=None, distance_m=None, group_fare=None,
                walk_minutes=None, provider=PROVIDER, fetched_at=_now().isoformat())
        return [edge(pid) for pid,_ in destinations]


async def _save_provider_route(origin, destination, route):
    """Keep completed legs even if a later fallback or origin exhausts quota."""
    summary = route.get("summary")
    if not isinstance(summary, dict):
        return
    duration, distance = summary.get("duration"), summary.get("distance")
    point = route.get("_point_route", False)
    fare_data = summary.get("fare")
    fare = fare_data.get("toll") if point and isinstance(fare_data,dict) else None if point else 0
    if (route.get("result_code") == 0
            and type(duration) is int and 0 <= duration <= 86400
            and type(distance) is int and 0 <= distance <= 10**9
            and type(fare) is int and 0 <= fare <= 10**9):
        edge = dict(status="KNOWN", duration_minutes=math.ceil(duration/60), distance_m=distance,
                    group_fare=fare, walk_minutes=0, fetched_at=_now().isoformat(),
                    provider="KAKAO_MOBILITY_DIRECTIONS_V1" if point else PROVIDER)
        await asyncio.to_thread(_save_routes, [(_cache_key(origin,destination),edge)])


async def _point_and_cache(client, semaphore, origin, destination):
    route = await _point_route(client,semaphore,origin,destination)
    await _save_provider_route(origin,destination,route)
    return route


async def _fetch_routes_from_origin(client, semaphore, origin_id, origin, destinations, day):
    edges = []
    for batch in _chunk(destinations, 30):
        payload = {
            "origin": {"x": origin["longitude"], "y": origin["latitude"]},
            "destinations": [
                {"key": dest_id, "x": point["longitude"], "y": point["latitude"]}
                for dest_id, point in batch
            ],
            "radius": KAKAO_ROUTE_RADIUS,
            "priority": "TIME",
            "avoid": ["toll"],
        }
        single_selected = getattr(client, "_travelsync_selected_legs", False) and len(batch) == 1
        point_limit_reason = None
        by_key = None
        try:
            if single_selected:
                pid, point = batch[0]
                try:
                    by_key = {pid: await _point_route(client, semaphore, origin, point)}
                except HTTPException as exc:
                    if exc.detail not in ("routing_quota_exceeded", "routing_daily_budget_exceeded"):
                        raise
                    point_limit_reason = exc.detail
            if by_key is None:
                response = await _route_request(client, semaphore, "POST", KAKAO_MULTI_DESTINATION_URL, json=payload)
                body = response.json()
                by_key = {r.get("key"): r for r in body.get("routes", []) if isinstance(r, dict)}
        except HTTPException as exc:
            per_plan_total_spent = getattr(client, "_travelsync_route_requests", 0) >= min(
                getattr(client, "_travelsync_plan_limit", KAKAO_ROUTE_INITIAL_REQUESTS), KAKAO_ROUTE_MAX_REQUESTS)
            if (single_selected or exc.detail not in ROUTING_LIMITS
                    or (exc.detail == "routing_request_budget_exceeded" and per_plan_total_spent)):
                raise
            # Independent point quota can still serve this batch. This path is
            # bounded by the same per-plan total/share limits as normal fallback.
            client._travelsync_limit_reason = exc.detail
            by_key = {pid: {"result_code": 304} for pid,_ in batch}
        except httpx.HTTPError:
            raise HTTPException(503, "routing_provider_unavailable") from None
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(502, "routing_invalid_response") from None

        # 304 means outside the batch search radius, not no driving route.
        # Only those legs use the unrestricted point-to-point endpoint.
        for pid, point in batch:
            await _save_provider_route(origin, point, by_key.get(pid, {}))
        outside = [(pid, point) for pid, point in batch
                   if by_key.get(pid, {}).get("result_code") == 304 and not by_key[pid].get("_point_route")]
        if outside and not point_limit_reason:
            fallback = await _gather_routes((
                _point_and_cache(client, semaphore, origin, point) for _, point in outside))
            by_key.update({pid: route for (pid, _), route in zip(outside, fallback)})
        elif outside:
            # Radius overflow with unavailable point routing is unknown.
            client._travelsync_limit_reason = point_limit_reason
            for pid, _ in outside:
                by_key.pop(pid)

        fetched = _now().isoformat()
        for dest_id, _ in batch:
            route = by_key.get(dest_id)
            if route is None:
                edges.append({
                    "date": day, "origin": origin_id, "destination": dest_id,
                    "status": "UNKNOWN", "duration_minutes": None, "distance_m": None,
                    "group_fare": None, "walk_minutes": None,
                    "provider": PROVIDER, "fetched_at": fetched,
                })
                continue
            if route.get("result_code") not in (None, 0):
                edges.append({
                    "date": day, "origin": origin_id, "destination": dest_id,
                    "status": "UNREACHABLE", "duration_minutes": None, "distance_m": None,
                    "group_fare": None, "walk_minutes": None,
                    "provider": PROVIDER, "fetched_at": fetched,
                })
                continue
            summary = route.get("summary")
            duration = summary.get("duration") if isinstance(summary, dict) else None
            distance = summary.get("distance") if isinstance(summary, dict) else None
            fare = 0
            if route.get("_point_route"):
                fare_data = summary.get("fare") if isinstance(summary, dict) else None
                fare = fare_data.get("toll") if isinstance(fare_data, dict) else None
            if (route.get("result_code") != 0
                    or type(duration) is not int or not 0 <= duration <= 86400
                    or type(distance) is not int or not 0 <= distance <= 10**9
                    or type(fare) is not int or not 0 <= fare <= 10**9):
                edges.append({
                    "date": day, "origin": origin_id, "destination": dest_id,
                    "status": "UNKNOWN", "duration_minutes": None, "distance_m": None,
                    "group_fare": None, "walk_minutes": None,
                    "provider": PROVIDER, "fetched_at": fetched,
                })
                continue
            edges.append({
                "date": day, "origin": origin_id, "destination": dest_id,
                "status": "KNOWN",
                "duration_minutes": math.ceil(duration / 60),
                "distance_m": distance,
                "group_fare": fare,
                "walk_minutes": 0,
                "provider": ("KAKAO_MOBILITY_DIRECTIONS_V1" if route.get("_point_route") else PROVIDER),
                "fetched_at": fetched,
            })
        await asyncio.to_thread(_save_routes, [(_cache_key(origin, point), edge)
            for (pid,point),edge in zip(batch, edges[-len(batch):])])
    return edges


class RouteEdges(list):
    warnings = ()


async def _build_selected_edges(request):
    legs = request.get("route_legs")
    spent = request.get("routing_attempts", {})
    expand = request.get("expand_routing_budget", False)
    if (not isinstance(legs, list) or len(legs) > 63 or not isinstance(spent, dict)
            or set(spent) - {"batch", "point"} or type(expand) is not bool
            or any(type(v) is not int or v < 0 for v in spent.values())):
        raise HTTPException(422, "routing_invalid_selected_legs")
    edges = RouteEdges()
    if not legs:
        return edges, spent
    if not KAKAO_REST_API_KEY:
        raise HTTPException(503, "kakao_rest_api_key_not_configured")
    days = {day["date"]: day for day in request["days"]}
    points = {p["poi_id"]: p for p in request["pois"]} | request["points"]
    grouped = {}
    seen = set()
    for leg in legs:
        if not isinstance(leg, dict) or any(not isinstance(leg.get(k), str) for k in ("date", "origin", "destination")):
            raise HTTPException(422, "routing_invalid_selected_legs")
        day, origin, destination = (leg[k] for k in ("date", "origin", "destination"))
        key = (day, origin, destination)
        if day not in days or origin == destination or key in seen:
            raise HTTPException(422, "routing_invalid_selected_legs")
        candidates = set(request["day_candidate_ids"].get(day, []))
        start, end = days[day]["start_point"], days[day]["end_point"]
        if (origin not in candidates | {start} or destination not in candidates | {end}
                or origin not in points or destination not in points or (origin == start and destination == end)):
            raise HTTPException(422, "routing_invalid_selected_legs")
        seen.add(key)
        grouped.setdefault((day, origin), []).append((destination, points[destination]))
    semaphore = asyncio.Semaphore(KAKAO_ROUTE_CONCURRENCY)
    async with httpx.AsyncClient(headers={"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"},
                                 timeout=httpx.Timeout(15.0)) as client:
        client._travelsync_selected_legs = True
        client._travelsync_endpoint_requests = dict(spent)
        client._travelsync_route_requests = sum(spent.values())
        if expand:
            client._travelsync_plan_limit = KAKAO_ROUTE_MAX_REQUESTS
        batches = await _gather_routes(_routes_from_origin(client, semaphore, origin, points[origin], targets, day)
                                       for (day, origin), targets in grouped.items())
        edges.extend(edge for batch in batches for edge in batch)
        limit = getattr(client, "_travelsync_limit_reason", None)
        if limit and any(e["status"] == "UNKNOWN" for e in edges):
            edges.warnings = (limit,)
        return edges, client._travelsync_endpoint_requests


def _assemble_edges(nested, layouts):
    edges = RouteEdges()
    for batch, (first, days) in zip(nested, layouts):
        for d in days:
            rename = {first["start_point"]: d["start_point"], first["end_point"]: d["end_point"]}
            edges.extend({**e, "date": d["date"],
                          "origin": rename.get(e["origin"], e["origin"]),
                          "destination": rename.get(e["destination"], e["destination"])} for e in batch)
    return edges


def _needs_route_expansion(request, edges):
    """Missing round-trip connections justify a second retrieval pass.

    This is only a graph check. Time windows, meals and full feasibility still
    belong to the optimizer/E3. Known-unreachable legs are not blindly retried.
    """
    for d in request.get("days", []):
        today = [e for e in edges if e["date"] == d["date"]]
        if not any(e["status"] == "UNKNOWN" for e in today):
            continue
        def reachable(start, reverse=False):
            found, todo = {start}, [start]
            while todo:
                at = todo.pop()
                for e in today:
                    if e["status"] != "KNOWN":
                        continue
                    a,b = (e["destination"],e["origin"]) if reverse else (e["origin"],e["destination"])
                    if a == at and b not in found:
                        found.add(b); todo.append(b)
            return found
        usable = reachable(d["start_point"]) & reachable(d["end_point"], True)
        ids = request.get("day_candidate_ids", {}).get(d["date"], [p["poi_id"] for p in request.get("pois", [])])
        if any(pid not in usable for pid in ids):
            return True
    return False


async def _build_drive_edges(request: dict) -> list[dict]:
    if not KAKAO_REST_API_KEY:
        raise HTTPException(503, "kakao_rest_api_key_not_configured")
    poi_points = {
        p["poi_id"]: {"latitude": p["latitude"], "longitude": p["longitude"]}
        for p in request.get("pois", [])
    }
    fixed_points = request.get("points", {})
    semaphore = asyncio.Semaphore(KAKAO_ROUTE_CONCURRENCY)
    headers = {
        "Authorization": f"KakaoAK {KAKAO_REST_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(15.0)) as client:
        tasks = []
        groups = {}
        for d in request.get("days", []):
            signature = (tuple(sorted(request.get("day_candidate_ids", {}).get(d["date"], poi_points))),) + tuple(fixed_points[d[k]][coord] for k in ("start_point", "end_point")
                              for coord in ("latitude", "longitude"))
            groups.setdefault(signature, []).append(d)
        layouts = []
        for days in groups.values():
            d = days[0]
            day = d["date"]
            start_id = d["start_point"]
            end_id = d["end_point"]
            start = fixed_points[start_id]
            end = fixed_points[end_id]
            day_ids = set(request.get("day_candidate_ids", {}).get(day, poi_points))
            day_points = {pid: p for pid, p in poi_points.items() if pid in day_ids}
            origins = [(start_id, start), *day_points.items()]
            for origin_id, origin in origins:
                targets = [(dest_id, point) for dest_id, point in day_points.items()
                           if dest_id != origin_id]
                # Secure return legs before optional inter-POI alternatives.
                targets.insert(0, (end_id, end))
                if origin_id == start_id:
                    targets = [x for x in targets if x[0] != end_id]
                tasks.append((origin_id, origin, targets, day))
                layouts.append((d, days))
        async def fetch_pass():
            nested = await _gather_routes(_routes_from_origin(client, semaphore, *args) for args in tasks)
            return _assemble_edges(nested, layouts)
        edges = await fetch_pass()
        initial = min(KAKAO_ROUTE_INITIAL_REQUESTS, KAKAO_ROUTE_MAX_REQUESTS)
        if (getattr(client, "_travelsync_route_requests", 0) >= initial
                and initial < KAKAO_ROUTE_MAX_REQUESTS and _needs_route_expansion(request, edges)):
            # Reuse this client's counters and the persistent cache: two passes
            # share one hard cap, and completed legs are not fetched twice.
            client._travelsync_plan_limit = KAKAO_ROUTE_MAX_REQUESTS
            client._travelsync_limit_reason = None
            edges = await fetch_pass()
        limit_reason = getattr(client, "_travelsync_limit_reason", None)
    if limit_reason:
        if not any(e["status"] == "KNOWN" for e in edges):
            raise HTTPException(503, limit_reason)
        edges.warnings = (limit_reason,) if any(e["status"] == "UNKNOWN" for e in edges) else ()
    return edges


@app.get("/health")
def health():
    return {
        "ok": True,
        "module": "planning-data",
        "schema_version": "planning-data-1.0",
        "kakao_configured": bool(KAKAO_REST_API_KEY),
        "routing_guard": {"enabled": True, "concurrency": KAKAO_ROUTE_CONCURRENCY,
                          "initial_requests_per_plan": KAKAO_ROUTE_INITIAL_REQUESTS,
                          "max_requests_per_plan": KAKAO_ROUTE_MAX_REQUESTS,
                          "endpoint_share_percent": KAKAO_ROUTE_PLAN_QUOTA_SHARE_PERCENT},
        "route_cache": {"persistent": True, "expires": False, "mode": "demo_snapshot"},
    }


@app.post("/snapshot")
async def snapshot(request: dict, authorization: str | None = Header(default=None)):
    return await _snapshot(request, authorization, selected=False)


@app.post("/selected-snapshot")
async def selected_snapshot(request: dict, authorization: str | None = Header(default=None)):
    return await _snapshot(request, authorization, selected=True)


async def _snapshot(request, authorization, *, selected):
    _auth(authorization)
    if request.get("schema_version") != "planning-data-1.0":
        raise HTTPException(422, "planning_data_schema_mismatch")
    if request.get("transport_mode") != "drive":
        raise HTTPException(422, "planning_data_only_drive_supported")

    poi_ids = [p["poi_id"] for p in request.get("pois", [])]
    day_values = [date.fromisoformat(d["date"]) for d in request.get("days", [])]
    operating = await asyncio.to_thread(_load_operating, request["dataset_version"], poi_ids, day_values)
    if selected:
        edges, spent = await _build_selected_edges(request)
    else:
        edges = await _build_drive_edges(request)
    for day in (() if selected else request.get("days", [])):
        departure_edges = [e for e in edges if e["date"] == day["date"] and e["origin"] == day["start_point"]]
        return_edges = [e for e in edges if e["date"] == day["date"] and e["destination"] == day["end_point"]]
        if departure_edges and all(e["status"] == "UNREACHABLE" for e in departure_edges):
            raise HTTPException(422, "routing_start_point_unreachable")
        if return_edges and all(e["status"] == "UNREACHABLE" for e in return_edges):
            raise HTTPException(422, "routing_end_point_unreachable")
    if not selected and edges and not any(edge["status"] == "KNOWN" for edge in edges):
        code = ("routing_no_routes" if all(edge["status"] == "UNREACHABLE" for edge in edges)
                else "routing_invalid_response")
        raise HTTPException(502, code)

    snapshot_basis = {
        "dataset_version": request["dataset_version"],
        "days": [d.isoformat() for d in day_values],
        "poi_ids": poi_ids,
        "created_at": _now().isoformat(),
    }
    return {
        "schema_version": "planning-data-1.0",
        "context_id": request["context_id"],
        "dataset_version": request["dataset_version"],
        "transport_mode": "drive",
        "operating_snapshot_id": "travel-db-" + _hash(snapshot_basis)[:16],
        "routing_version": ROUTING_VERSION,
        **({"routing_attempts": spent} if selected else {}),
        "routing_warnings": list(getattr(edges, "warnings", ())),
        "pois": [operating[pid] for pid in poi_ids if pid in operating],
        "edges": edges,
    }


@app.get("/diagnostics")
def diagnostics(authorization: str | None = Header(default=None)):
    _auth(authorization)
    with engine.connect() as c:
        return {**dataset_diagnostics(c), "routing_budget": _budget_status()}
