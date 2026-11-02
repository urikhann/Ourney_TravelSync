"""Load opening hours from the data teammate's operating-info CSV into poi_hours / poi_exceptions.

    cd api
    python -m app.import_hours ../src/ourney_travelsync/data/travelsync_poi_operating_info_v1.csv

Safe to run again: for every place in the file, its old hours and exceptions are replaced.
The places themselves (pois table) must already be imported; their field_status says what we know:
  hours:        KNOWN (weekly) | SNAPSHOT (only one observed week) | UNKNOWN
  closed_days:  KNOWN | PARTIAL | UNKNOWN
See app/hours.py for the rules.
"""
import argparse
import csv
from collections import Counter
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .hours import parse_row
from .models import Poi, PoiException, PoiHours


def import_hours(db: Session, rows: list[dict]) -> Counter:
    stats = Counter()
    by_id: dict[str, list[Poi]] = {}
    for p in db.scalars(select(Poi)):
        by_id.setdefault(p.id, []).append(p)  # the same place can exist in several dataset versions
    ids = [r["place_id"].strip() for r in rows if (r.get("place_id") or "").strip() in by_id]
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        db.execute(delete(PoiHours).where(PoiHours.poi_id.in_(part)))
        db.execute(delete(PoiException).where(PoiException.poi_id.in_(part)))
    for r in rows:
        pid = (r.get("place_id") or "").strip()
        if pid not in by_id:
            stats["not_in_db"] += 1
            continue
        h = parse_row(r)
        stats[f"hours_{h.hours}"] += 1
        for dow, (o, c) in sorted(h.weekly.items()):
            db.add(PoiHours(poi_id=pid, dow=dow, open=o, close=c))
        for day, o, c in h.dated:
            db.add(PoiException(poi_id=pid, day=day, closed=o is None, open=o, close=c,
                                reason="closed that day (seen in one week of data)" if o is None else "hours seen for this date"))
        for p in by_id[pid]:
            p.field_status = {**(p.field_status or {}), "hours": h.hours, "closed_days": h.closed_days}
            p.closed_on_public_holidays = h.closed_on_public_holidays
    db.commit()
    return stats


if __name__ == "__main__":
    from .db import Base, SessionLocal, engine

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    a = ap.parse_args()
    Base.metadata.create_all(engine)
    with open(Path(a.csv), encoding="utf-8-sig", newline="") as fh:
        data = list(csv.DictReader(fh))
    with SessionLocal() as s:
        result = import_hours(s, data)
    print(f"Read {len(data)} rows.")
    print(f"  weekly hours (KNOWN):        {result['hours_KNOWN']}")
    print(f"  one observed week (SNAPSHOT): {result['hours_SNAPSHOT']}")
    print(f"  unknown:                      {result['hours_UNKNOWN']}")
    if result["not_in_db"]:
        print(f"  skipped, place not imported: {result['not_in_db']}")
