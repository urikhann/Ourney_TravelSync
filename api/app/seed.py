"""Validate explicitly installed deployment data; sample seeding requires PLACES_CSV=."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from . import config
from .features import FEATURE_SCHEMA_VERSION
from .import_places import active_version
from .models import DatasetVersion, Poi, PoiHours, PublicHoliday

# Sample Korean public holidays; import a complete calendar for scheduling.
HOLIDAYS_2026 = [(date(2026, 10, 3), "National Foundation Day (개천절)"),
                 (date(2026, 10, 9), "Hangul Day (한글날)"),
                 (date(2026, 12, 25), "Christmas Day")]
SAMPLE_VERSION = "sample-v1"


def _sample(db: Session) -> int:
    data = json.loads((Path(__file__).parent / "data" / "sample_pois.json").read_text(encoding="utf-8"))
    db.add(DatasetVersion(version=SAMPLE_VERSION, status="ACTIVE", feature_schema_version=FEATURE_SCHEMA_VERSION,
                          mapping_version="hand-made", poi_count=len(data["pois"]), description="28 sample places",
                          activated_at=datetime.now(timezone.utc)))
    db.flush()
    for p in data["pois"]:
        hours = p.pop("hours")
        db.add(Poi(**p, dataset_version=SAMPLE_VERSION, source="sample",
                   field_status={"hours": "KNOWN", "cost": "KNOWN", "duration": "ESTIMATED"}))
        for h in hours:
            db.add(PoiHours(poi_id=p["id"], dow=h["dow"], open=h["open"], close=h["close"]))
    db.commit()
    return len(data["pois"])


def seed_if_empty(db: Session) -> int:
    if config.PLACES_CSV:
        from .deployment import check_startup
        return check_startup(db)
    for d, name in HOLIDAYS_2026:
        if not db.get(PublicHoliday, d):
            db.add(PublicHoliday(day=d, name=name))
    db.commit()
    if active_version(db):
        return 0
    return _sample(db)
