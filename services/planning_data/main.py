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

import httpx
from fastapi import FastAPI, Header, HTTPException
from sqlalchemy import bindparam, create_engine, text

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://travelsync:travelsync@db:5432/travelsync",
)
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
PLANNING_DATA_TOKEN = os.getenv("PLANNING_DATA_TOKEN", "")
KAKAO_ROUTE_RADIUS = min(10000, max(1, int(os.getenv("KAKAO_ROUTE_RADIUS", "5000"))))
KAKAO_ROUTE_CONCURRENCY = max(1, int(os.getenv("KAKAO_ROUTE_CONCURRENCY", "8")))

ROUTE_CACHE_PATH = os.getenv("ROUTE_CACHE_PATH", "/cache/routes.sqlite3")
_origin_locks = weakref.WeakKeyDictionary()

KAKAO_MULTI_DESTINATION_URL = "https://apis-navi.kakaomobility.com/v1/destinations/directions"
KAKAO_DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"
logger = logging.getLogger(__name__)
PROVIDER = "KAKAO_MOBILITY_MULTI_DESTINATION_V1_TOLL_FREE"
ROUTING_VERSION = "kakao-drive-multidestination-with-point-fallback-v2"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
app = FastAPI(title="TravelSync planning data", version="1.0")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return _now().isoformat()
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
        pois = {r["id"]: dict(r) for r in c.execute(
            poi_sql, {"version": dataset_version, "ids": poi_ids}
        ).mappings()}
        hour_rows = [dict(r) for r in c.execute(hours_sql, {"ids": poi_ids}).mappings()]
        exc_rows = [dict(r) for r in c.execute(
            exc_sql, {"ids": poi_ids, "days": days}
        ).mappings()] if days else []
        holiday_rows = [dict(r) for r in c.execute(
            holiday_sql, {"days": days}
        ).mappings()] if days else []

    hours: dict[str, dict[int, list[dict]]] = {}
    for r in hour_rows:
        hours.setdefault(r["poi_id"], {}).setdefault(r["dow"], []).append(r)
    exceptions = {(r["poi_id"], r["day"]): r for r in exc_rows}
    holidays = {r["day"]: r["name"] for r in holiday_rows}

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
    for attempt in range(3):
        async with semaphore:
            response = await client.request(method, url, **kwargs)
        transient = (response.status_code in (429, 500, 502, 503, 504)
                     or (response.status_code == 400 and _provider_error_code(response) == -10))
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
            fresh = await _fetch_routes_from_origin(client, semaphore, origin_id, origin, misses, day)
        else:
            fresh = []
        by_id = {e["destination"]: e for e in fresh}
        return [{**cached[keys[pid]], "date": day, "origin": origin_id, "destination": pid}
                if keys[pid] in cached else by_id[pid] for pid,_ in destinations]


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
        try:
            response = await _route_request(client, semaphore, "POST", KAKAO_MULTI_DESTINATION_URL, json=payload)
            body = response.json()
            by_key = {r.get("key"): r for r in body.get("routes", []) if isinstance(r, dict)}
        except httpx.HTTPError:
            raise HTTPException(503, "routing_provider_unavailable") from None
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(502, "routing_invalid_response") from None

        # 304 means outside the batch search radius, not no driving route.
        # Only those legs use the unrestricted point-to-point endpoint.
        outside = [(pid, point) for pid, point in batch
                   if by_key.get(pid, {}).get("result_code") == 304]
        if outside:
            fallback = await _gather_routes((
                _point_route(client, semaphore, origin, point) for _, point in outside))
            by_key.update({pid: route for (pid, _), route in zip(outside, fallback)})

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
                targets.append((end_id, end))
                if origin_id == start_id:
                    targets = [x for x in targets if x[0] != end_id]
                tasks.append(_routes_from_origin(
                    client, semaphore, origin_id, origin, targets, day
                ))
                layouts.append((d, days))
        nested = await _gather_routes(tasks)
    edges = []
    for batch, (first, days) in zip(nested, layouts):
        for d in days:
            rename = {first["start_point"]: d["start_point"], first["end_point"]: d["end_point"]}
            edges.extend({**e, "date": d["date"],
                          "origin": rename.get(e["origin"], e["origin"]),
                          "destination": rename.get(e["destination"], e["destination"])} for e in batch)
    return edges


@app.get("/health")
def health():
    return {
        "ok": True,
        "module": "planning-data",
        "schema_version": "planning-data-1.0",
        "kakao_configured": bool(KAKAO_REST_API_KEY),
        "route_cache": {"persistent": True, "expires": False, "mode": "demo_snapshot"},
    }


@app.post("/snapshot")
async def snapshot(request: dict, authorization: str | None = Header(default=None)):
    _auth(authorization)
    if request.get("schema_version") != "planning-data-1.0":
        raise HTTPException(422, "planning_data_schema_mismatch")
    if request.get("transport_mode") != "drive":
        raise HTTPException(422, "planning_data_only_drive_supported")

    poi_ids = [p["poi_id"] for p in request.get("pois", [])]
    day_values = [date.fromisoformat(d["date"]) for d in request.get("days", [])]
    operating = await asyncio.to_thread(_load_operating, request["dataset_version"], poi_ids, day_values)
    edges = await _build_drive_edges(request)
    for day in request.get("days", []):
        departure_edges = [e for e in edges if e["date"] == day["date"] and e["origin"] == day["start_point"]]
        return_edges = [e for e in edges if e["date"] == day["date"] and e["destination"] == day["end_point"]]
        if departure_edges and all(e["status"] == "UNREACHABLE" for e in departure_edges):
            raise HTTPException(422, "routing_start_point_unreachable")
        if return_edges and all(e["status"] == "UNREACHABLE" for e in return_edges):
            raise HTTPException(422, "routing_end_point_unreachable")
    if edges and not any(edge["status"] == "KNOWN" for edge in edges):
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
        "pois": [operating[pid] for pid in poi_ids if pid in operating],
        "edges": edges,
    }
