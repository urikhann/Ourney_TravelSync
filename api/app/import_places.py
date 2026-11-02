"""Import the data pipeline's CSV (travelsync_final_places.csv) as a new dataset version.

    cd api
    python -m app.import_places ../src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v1.csv --version busan-enriched-v1 --mapping rule_v2

What it does (SRS FR-POI-DATA-01 / SDD 6.11, simplified for v1):
  1. checks every row (id, name, Busan coordinates, 10 features that are exactly 0 or 1, at least one 1)
  2. writes the places as a DRAFT version
  3. only if everything passed: makes it ACTIVE and archives the old ACTIVE version
If anything fails, nothing is changed — the old ACTIVE version keeps working.
"""
import argparse
import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .features import FEATURE_SCHEMA_VERSION, FEATURES
from .metadata import clean_meta
from .models import DatasetVersion, Poi

CORE_COLUMNS = {"place_id", "place_name", "category", "category_name", "cat1", "cat2", "cat3",
                "address", "road_address", "latitude", "longitude", "phone", "place_url", "image_url",
                "source", "source_place_id", "kakao_place_ids", "feature_rules", "exclude_reason", *FEATURES}

REQUIRED = ["place_id", "place_name", "latitude", "longitude", *FEATURES]
BUSAN_BOX = (34.8, 35.5, 128.7, 129.4)  # lat min/max, lng min/max — a sanity check, not an exact border
STAY_MIN = {"food": 70, "cafe": 60, "nature": 90, "history": 75, "art": 75, "shopping": 90,
            "activity": 90, "entertainment": 120, "relax": 60, "festival": 120}
STAY_RULE = "stay-rule-1"  # category default stay times are ESTIMATED (SDD 7.2)
TAG_RULES = {
    "hiking": re.compile(r"등산|둘레길|트레킹|갈맷길|숲길"),  # 갈맷길 = Busan's long walking trails
    "raw_fish": re.compile(r"횟집|회센터|회집|물회|활어|초밥|스시|사시미|오마카세"),
    "alcohol": re.compile(r"술집|주점|호프|포차|이자카야|와인바|칵테일바"),
}
DISTRICT = re.compile(r"부산(?:광역시)?\s+(\S+?[구군])\b")


class ImportProblem(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems[:10]))
        self.problems = problems


def _tags(name: str, category: str, f: dict) -> list[str]:
    text = f"{category} {name}"
    tags = [t for t, rx in TAG_RULES.items() if rx.search(text)]
    if "hiking" not in tags and f["nature"] and re.search(r"(산|봉)$", name.strip()):
        tags.append("hiking")  # mountains and peaks, e.g. 금정산, 연대봉
    return tags


def row_to_poi(row: dict, version: str) -> Poi:
    f = {k: int(row[k]) for k in FEATURES}
    primary = next(k for k in FEATURES if f[k])
    category = row.get("category_name") or row.get("category") or ""
    name = row["place_name"].strip()
    addr = row.get("address", "")
    m = DISTRICT.search(addr) or DISTRICT.search(row.get("road_address", ""))
    indoor = None
    if f["nature"] or f["relax"]:
        indoor = False
    elif f["cafe"] or f["food"] or f["art"] or f["shopping"] or f["entertainment"]:
        indoor = True
    return Poi(
        id=row["place_id"].strip(), dataset_version=version, name=name, name_ko=name, city="busan",
        area=m.group(1) if m else "", kind="food" if f["food"] else "activity",
        category=category, address=addr, road_address=row.get("road_address", ""), phone=row.get("phone", ""),
        place_url=row.get("place_url", ""), image_url=row.get("image_url", ""),
        lat=float(row["latitude"]), lng=float(row["longitude"]), indoor=indoor,
        avg_cost_krw=None, duration_min=STAY_MIN[primary],
        enriched_metadata=clean_meta({k: v for k, v in row.items() if k not in CORE_COLUMNS}),  # "NULL" -> None
        tags=_tags(name, category, f), features=f, feature_rules=(row.get("feature_rules") or "")[:500],
        field_status={"hours": "UNKNOWN", "closed_days": "UNKNOWN", "cost": "UNKNOWN",
                      "duration": "ESTIMATED", "duration_rule": STAY_RULE},
        walking_level=0.6 if f["nature"] and f["activity"] else 0.4 if f["nature"] else 0.1,
        source=row.get("source") or "csv",
    )


def check_rows(rows: list[dict], columns: list[str]) -> list[str]:
    problems = [f"missing column '{c}'" for c in REQUIRED if c not in columns]
    if problems:
        return problems
    seen = set()
    for n, r in enumerate(rows, start=2):  # line 1 is the header
        pid = (r["place_id"] or "").strip()
        if not pid:
            problems.append(f"line {n}: empty place_id")
        elif pid in seen:
            problems.append(f"line {n}: duplicate place_id {pid}")
        seen.add(pid)
        if not (r["place_name"] or "").strip():
            problems.append(f"line {n}: empty place_name")
        try:
            lat, lng = float(r["latitude"]), float(r["longitude"])
            if not (BUSAN_BOX[0] <= lat <= BUSAN_BOX[1] and BUSAN_BOX[2] <= lng <= BUSAN_BOX[3]):
                problems.append(f"line {n}: coordinates outside Busan ({lat}, {lng})")
        except (TypeError, ValueError):
            problems.append(f"line {n}: invalid coordinates")
        bad = [k for k in FEATURES if (r[k] or "").strip() not in ("0", "1")]
        if bad:
            problems.append(f"line {n}: features must be exactly 0 or 1 ({', '.join(bad)})")
        elif not any(r[k].strip() == "1" for k in FEATURES):
            problems.append(f"line {n}: all 10 features are 0")
    return problems


def import_rows(db: Session, rows: list[dict], columns: list[str], version: str, mapping_version: str,
                description: str = "", activate: bool = True) -> int:
    if db.get(DatasetVersion, version):
        raise ImportProblem([f"dataset version '{version}' already exists — versions never change, pick a new name"])
    problems = check_rows(rows, columns)
    if problems:
        raise ImportProblem(problems)
    try:
        db.add(DatasetVersion(version=version, status="DRAFT", feature_schema_version=FEATURE_SCHEMA_VERSION,
                              mapping_version=mapping_version, poi_count=len(rows), description=description))
        db.flush()
        db.add_all(row_to_poi(r, version) for r in rows)
        db.flush()
        if activate:
            db.execute(update(DatasetVersion).where(DatasetVersion.status == "ACTIVE").values(status="ARCHIVED"))
            dv = db.get(DatasetVersion, version)
            dv.status, dv.activated_at = "ACTIVE", datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(rows)


def import_csv(db: Session, path: str | Path, version: str, mapping_version: str = "rule_v2",
               activate: bool = True) -> int:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        columns = reader.fieldnames or []
    return import_rows(db, rows, columns, version, mapping_version, f"CSV import of {Path(path).name}", activate)


def active_version(db: Session) -> str | None:
    return db.scalar(select(DatasetVersion.version).where(DatasetVersion.status == "ACTIVE"))


if __name__ == "__main__":
    from .db import Base, SessionLocal, engine, ensure_enriched_metadata_column

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--version", required=True, help="new dataset version name, e.g. busan-2026-10-v1")
    ap.add_argument("--mapping", default="rule_v2", help="the data pipeline's rule version")
    ap.add_argument("--draft", action="store_true", help="import as DRAFT only, do not activate")
    a = ap.parse_args()
    Base.metadata.create_all(engine)
    ensure_enriched_metadata_column()
    with SessionLocal() as s:
        try:
            n = import_csv(s, a.csv, a.version, a.mapping, activate=not a.draft)
        except ImportProblem as e:
            print("Import refused, nothing changed.")
            for p in e.problems[:50]:
                print("  -", p)
            raise SystemExit(1)
        print(f"Imported {n} places as {a.version} ({'DRAFT' if a.draft else 'ACTIVE'}).")
