"""Fills an empty database with places so the app works out of the box.

- If the real data file exists (PLACES_CSV, default: the enriched snapshot inside the recommender package,
  src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v3.csv), it is imported as the ACTIVE dataset version
  PLACES_VERSION ("busan-enriched-v3") — the 3,570 Busan places from the data pipeline.
- Otherwise (and in the tests) the 28 hand-made sample places are loaded as dataset version "sample-v1".
Runs on startup only when there is no ACTIVE dataset version yet (opening hours are loaded right after the places).
Later updates: python -m app.import_places ...  In Docker (PLACES_FOLLOW_ENV=true) it follows PLACES_CSV / PLACES_VERSION.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from . import config
from .features import FEATURE_SCHEMA_VERSION
from .import_hours import import_hours_csv
from .import_places import active_version, import_csv
from .models import DatasetVersion, Poi, PoiHours, PublicHoliday

# Sample: a few 2026 Korean public holidays. The data pipeline replaces this with the full list.
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


def _import_with_hours(db: Session, csv_path: Path) -> int:
    n = import_csv(db, csv_path, version=config.PLACES_VERSION, mapping_version="rule_v2")
    hours = Path(config.HOURS_CSV) if config.HOURS_CSV else None
    if hours and hours.is_file():
        import_hours_csv(db, hours)  # safe to repeat; also sets the hours flags of the new places
    return n


def _follow_env(db: Session, csv_path: Path) -> int:
    """Docker mode: the configured version is imported if missing, otherwise made ACTIVE."""
    v = config.PLACES_VERSION
    if not db.get(DatasetVersion, v):
        return _import_with_hours(db, csv_path)
    if active_version(db) != v:
        db.execute(update(DatasetVersion).where(DatasetVersion.status == "ACTIVE").values(status="ARCHIVED"))
        dv = db.get(DatasetVersion, v)
        dv.status, dv.activated_at = "ACTIVE", datetime.now(timezone.utc)
        db.commit()
    return 0


def seed_if_empty(db: Session) -> int:
    for d, name in HOLIDAYS_2026:
        if not db.get(PublicHoliday, d):
            db.add(PublicHoliday(day=d, name=name))
    db.commit()
    csv_path = Path(config.PLACES_CSV) if config.PLACES_CSV else None
    if config.PLACES_FOLLOW_ENV and csv_path and csv_path.is_file():
        return _follow_env(db, csv_path)
    if active_version(db):
        return 0
    if csv_path and csv_path.is_file():
        return _import_with_hours(db, csv_path)
    return _sample(db)
