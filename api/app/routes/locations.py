"""Find a start or end location for a trip day by name, instead of typing latitude and longitude.

    GET /trips/{trip_id}/locations?q=부산역

Results, best first:
  1. popular starting points in Busan (stations, airport, terminals, beaches), always available
  2. Kakao Local keyword search (hotels, addresses, anything on Kakao Map) when KAKAO_REST_API_KEY is set
  3. places from the active TravelSync catalogue
Each result: {"id", "name", "sub", "lat", "lng", "source": "popular" | "kakao" | "place"}.
Kakao answers are cached in memory, so typing the same word again costs no quota
(keyword search has its own, much larger daily quota than the route APIs).
"""
import logging
from collections import OrderedDict

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..deps import member_trip
from ..models import DatasetVersion, Poi, Trip

router = APIRouter(tags=["planning"])
log = logging.getLogger("uvicorn.error")

KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
BUSAN_CENTER = {"x": "129.0756", "y": "35.1796"}  # Kakao uses x = longitude, y = latitude

# Road-accessible points people usually start from. Coordinates are approximate entrances.
POPULAR = [
    {"id": "popular:busan-station", "name": "부산역", "name_en": "Busan Station (KTX)", "lat": 35.1151, "lng": 129.0414},
    {"id": "popular:gimhae-airport", "name": "김해국제공항", "name_en": "Gimhae International Airport", "lat": 35.1732, "lng": 128.9463},
    {"id": "popular:haeundae-station", "name": "해운대역", "name_en": "Haeundae Station", "lat": 35.1636, "lng": 129.1588},
    {"id": "popular:seomyeon-station", "name": "서면역", "name_en": "Seomyeon Station", "lat": 35.1578, "lng": 129.0592},
    {"id": "popular:nampo-station", "name": "남포역", "name_en": "Nampo Station", "lat": 35.0977, "lng": 129.0346},
    {"id": "popular:sasang-terminal", "name": "부산서부시외버스터미널 (사상)", "name_en": "Sasang Bus Terminal", "lat": 35.1646, "lng": 128.9790},
    {"id": "popular:nopo-terminal", "name": "부산종합버스터미널 (노포)", "name_en": "Busan Central Bus Terminal (Nopo)", "lat": 35.2834, "lng": 129.0950},
    {"id": "popular:haeundae-beach", "name": "해운대해수욕장", "name_en": "Haeundae Beach", "lat": 35.1587, "lng": 129.1604},
    {"id": "popular:gwangalli-beach", "name": "광안리해수욕장", "name_en": "Gwangalli Beach", "lat": 35.1532, "lng": 129.1186},
]

_cache: "OrderedDict[str, list[dict]]" = OrderedDict()
CACHE_SIZE = 500


def _popular(q: str) -> list[dict]:
    q = q.strip().lower()
    hits = [p for p in POPULAR if not q or q in p["name"].lower() or q in p["name_en"].lower()]
    return [{"id": p["id"], "name": p["name"], "sub": p["name_en"], "lat": p["lat"], "lng": p["lng"], "source": "popular"}
            for p in hits]


async def kakao_keyword(q: str) -> list[dict]:
    """Kakao Local keyword search near Busan. Any failure just means no Kakao results."""
    if not config.KAKAO_REST_API_KEY or len(q) < 2:
        return []
    if q in _cache:
        _cache.move_to_end(q)
        return _cache[q]
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(KAKAO_KEYWORD_URL, params={"query": q, **BUSAN_CENTER, "radius": 20000, "size": 10},
                                 headers={"Authorization": f"KakaoAK {config.KAKAO_REST_API_KEY}"})
        r.raise_for_status()
        docs = r.json().get("documents", [])
        out = [{"id": f"kakao:{d['id']}", "name": d["place_name"],
                "sub": d.get("road_address_name") or d.get("address_name") or "",
                "lat": float(d["y"]), "lng": float(d["x"]), "source": "kakao"} for d in docs]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("Kakao keyword search failed: %s", type(e).__name__)  # never log the key or the query
        return []
    _cache[q] = out
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return out


def _catalogue(db: Session, q: str) -> list[dict]:
    version = db.scalar(select(DatasetVersion.version).where(DatasetVersion.status == "ACTIVE"))
    if len(q) < 2 or not version:
        return []
    rows = db.scalars(select(Poi).where(Poi.dataset_version == version,
                                        or_(Poi.name.icontains(q, autoescape=True),
                                            Poi.address.icontains(q, autoescape=True)))
                      .order_by(Poi.name).limit(8))
    return [{"id": f"place:{p.id}", "name": p.name, "sub": p.road_address or p.address or p.area,
             "lat": p.lat, "lng": p.lng, "source": "place"} for p in rows]


@router.get("/trips/{trip_id}/locations")
async def search_locations(q: str = Query(default="", max_length=80), trip: Trip = Depends(member_trip),
                           db: Session = Depends(get_db)):
    q = q.strip()
    found = _popular(q) + await kakao_keyword(q) + _catalogue(db, q)
    seen, out = set(), []
    for r in found:  # the same spot from two sources is shown once
        key = (r["name"], round(r["lat"], 4), round(r["lng"], 4))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out[:20]
