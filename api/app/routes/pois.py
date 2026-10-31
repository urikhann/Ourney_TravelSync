"""Place details and the availability check for one place."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, services
from ..db import get_db
from ..import_places import active_version
from ..models import Poi, PoiHours, User
from ..places import place_dict
from .auth import current_user

router = APIRouter(tags=["places"])
DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
WEEK = [1, 2, 3, 4, 5, 6, 0]  # Mon … Sun


def _find(db: Session, poi_id: str, version: str | None) -> Poi:
    p = db.get(Poi, (poi_id, version or active_version(db)))
    if not p:
        raise HTTPException(404, "place_not_found")
    return p


@router.get("/pois/{poi_id}")
def get_poi(poi_id: str, version: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """One place. `version` = the dataset version of a run (default: the ACTIVE one)."""
    p = _find(db, poi_id, version)
    weekly = {h.dow: h for h in db.scalars(select(PoiHours).where(PoiHours.poi_id == poi_id))}
    # Monday first. A day missing from a known week = closed that day. No rows at all = hours unknown -> [].
    hours = [{"day": DAYS[d], "open": weekly[d].open if d in weekly else None,
              "close": weekly[d].close if d in weekly else None} for d in WEEK] if weekly else []
    return {**place_dict(p), "name_ko": p.name_ko, "city": p.city, "address": p.road_address or p.address,
            "phone": p.phone or None, "tags": p.tags, "feature_rules": p.feature_rules, "source": p.source,
            "dataset_version": p.dataset_version, "closed_on_public_holidays": p.closed_on_public_holidays,
            "hours": hours}


async def poi_availability(poi_id: str, day: date = Query(alias="date"), start: str = "14:00",
                           duration_min: int | None = None, user: User = Depends(current_user),
                           db: Session = Depends(get_db)):
    p = _find(db, poi_id, None)
    res = await services.check_availability({"checks": [{"poi_id": poi_id, "date": day.isoformat(), "start": start,
                                                          "duration_min": duration_min or p.duration_min}]})
    return res["results"][0]


# Only registered when the availability checker is switched on (see config.FEATURE_AVAILABILITY).
if config.FEATURE_AVAILABILITY:
    router.get("/pois/{poi_id}/availability")(poi_availability)
