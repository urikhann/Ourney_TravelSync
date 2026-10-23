"""Fills an empty database with places so the app works out of the box.

- If the real data file exists (PLACES_CSV, default ../data/places/travelsync_final_places.csv), it is imported
  as an ACTIVE dataset version — the 3,591 Busan places from the data pipeline.
- Otherwise (and in the tests) the 28 hand-made sample places are loaded as dataset version "sample-v1".
Runs on startup only when there is no ACTIVE dataset version yet. Later updates: python -m app.import_places ...
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from . import config
from .features import FEATURE_SCHEMA_VERSION
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


def seed_if_empty(db: Session) -> int:
    for d, name in HOLIDAYS_2026:
        if not db.get(PublicHoliday, d):
            db.add(PublicHoliday(day=d, name=name))
    db.commit()
    if active_version(db):
        return 0
    csv_path = Path(config.PLACES_CSV) if config.PLACES_CSV else None
    if csv_path and csv_path.is_file():
        return import_csv(db, csv_path, version="busan-csv-1", mapping_version="rule_v2")
    return _sample(db)
