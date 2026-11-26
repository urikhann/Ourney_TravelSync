"""Atomic explicit installation and read-only restart validation of the deployment snapshot."""
import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import JSON, Integer, Column, MetaData, String, Table, select, text, update
from sqlalchemy.orm import Session

from ourney_travelsync import deployment
from ourney_travelsync.operating_schedules import validate_schedule
from .hours import parse_row
from .import_places import check_rows, row_to_poi, active_version
from .models import DatasetVersion, Poi

schema = MetaData()
def records_table(name, *keys):
    return Table(name, schema, *(Column(k, Integer if k == "evidence_index" else String(80), primary_key=True) for k in keys), Column("payload", JSON, nullable=False))
installations = records_table("poi_deployment_installations", "dataset_version")
legacy = records_table("poi_versioned_legacy_hours", "dataset_version", "poi_id")
dated = records_table("poi_versioned_dated_hours", "dataset_version", "poi_id")
evidence = records_table("poi_enrichment_evidence", "dataset_version", "poi_id", "evidence_index")
reviews = records_table("poi_enrichment_reviews", "dataset_version", "poi_id")
# Evidence/review tables may already exist from the original promotion (index is INTEGER there).

def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def expected():
    spec, path = deployment.configured()
    m = deployment.manifest(spec)
    v = spec["dataset_version"]
    if m["dataset_version"] != v:
        raise ValueError("manifest version mismatch")
    rows = read_csv(path)
    problems = check_rows(rows, list(rows[0]))
    if problems or any(r["dataset_version"] != v for r in rows):
        raise ValueError("invalid deployment catalogue: " + str(problems[:3]))
    payload = m["payload"]
    if {r["place_id"] for r in rows} != set(payload["metadata"]):
        raise ValueError("manifest catalogue identity mismatch")
    ops = {r["place_id"]: parse_row(r) for r in read_csv(deployment.SNAPSHOT.parent / spec["operating_csv"])}
    pois, weekly = [], {}
    for row in rows:
        p = row_to_poi(row, v)
        p.enriched_metadata = payload["metadata"][p.id]
        h = ops[p.id]
        p.field_status = {**p.field_status, "hours": h.hours, "closed_days": h.closed_days}
        p.closed_on_public_holidays = h.closed_on_public_holidays
        if p.id in payload["suppress_legacy_hours_ids"]:
            p.field_status = {**p.field_status, "hours": "UNKNOWN", "closed_days": "UNKNOWN", "enrichment_review_required": True}
        weekly[p.id] = [{"poi_id": p.id, "dow": d, "open": o, "close": c} for d, (o,c) in sorted(h.weekly.items())]
        pois.append(p)
    for r in payload["schedules"]:
        validate_schedule(r["schedule"])
    return spec, payload, pois, weekly

def expected_dated():
    spec = deployment.snapshot()
    result = {}
    for r in read_csv(deployment.SNAPSHOT.parent / spec["operating_csv"]):
        h = parse_row(r)
        result[r["place_id"]] = [{"poi_id":r["place_id"],"day":d.isoformat(),"closed":o is None,"open":o,"close":c,
                                  "reason":"closed that day (seen in one week of data)" if o is None else "hours seen for this date"}
                                 for d,o,c in h.dated]
    return result

def _json(value):
    return json.loads(value) if isinstance(value, str) else value

def validate(db, spec, payload, pois, weekly, *, require_legacy=False):
    v = spec["dataset_version"]
    actual = {p.id:p for p in db.scalars(select(Poi).where(Poi.dataset_version == v)).unique()}
    dv = db.get(DatasetVersion, v)
    if not dv or dv.feature_schema_version != "fs-1.0" or dv.mapping_version != "rule_v2" or dv.poi_count != len(pois) or set(actual) != {p.id for p in pois}:
        raise ValueError("existing dataset catalogue/version mismatch")
    for p in pois:
        stored = actual[p.id]
        for key, value in p.__dict__.items():
            if key.startswith("_") or key == "metadata_record": continue
            if getattr(stored, key) != value:
                raise ValueError(f"existing dataset core mismatch: {p.id}/{key}")
        if stored.enriched_metadata != p.enriched_metadata:
            raise ValueError(f"existing dataset metadata mismatch: {p.id}")
    c = db.connection()
    from sqlalchemy import inspect
    for name in ["poi_normalized_hours", "poi_hours_import_batches"] + ([legacy.name, dated.name] if require_legacy else []):
        if not inspect(c).has_table(name): raise ValueError(f"missing snapshot table: {name}")
    for table, items in [(evidence, {(e["place_id"], str(e["evidence_index"])): e for e in payload["evidence"]}),
                         (reviews, {(r["place_id"],):r for r in payload["review"]})]:
        if not inspect(c).has_table(table.name): raise ValueError(f"missing snapshot table: {table.name}")
        rows = c.execute(select(table).where(table.c.dataset_version == v)).mappings()
        found = {(r["poi_id"], str(r["evidence_index"])) if table is evidence else (r["poi_id"],): r["payload"] for r in rows}
        if found != items: raise ValueError(f"existing dataset content mismatch: {table.name}")
    if not inspect(c).has_table("poi_normalized_hours"):
        raise ValueError("missing normalized schedules")
    rows = c.execute(text("SELECT poi_id,batch_id,schedule,evidence,observed_at FROM poi_normalized_hours WHERE dataset_version=:v"), {"v":v}).mappings()
    found = {r["poi_id"]: {k:_json(r[k]) if k in ("schedule","evidence") else r[k] for k in ("schedule","evidence","observed_at")} for r in rows}
    wanted = {r["place_id"]:{k:r[k] for k in ("schedule","evidence","observed_at")} for r in payload["schedules"]}
    if found != wanted: raise ValueError("existing dataset normalized hours mismatch")
    batch = "hours-" + v
    batches = c.execute(text("SELECT batch_id,payload_sha256,poi_count FROM poi_hours_import_batches WHERE dataset_version=:v"), {"v":v}).mappings().all()
    digest = hashlib.sha256(json.dumps(payload["schedules"], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    if len(batches) != 1 or dict(batches[0]) != {"batch_id":batch,"payload_sha256":digest,"poi_count":len(wanted)}:
        raise ValueError("existing dataset hours batch mismatch")
    if c.execute(text("SELECT count(*) FROM poi_normalized_hours WHERE dataset_version=:v AND batch_id<>:b"), {"v":v,"b":batch}).scalar_one():
        raise ValueError("unexpected normalized hours version")
    if inspect(c).has_table(legacy.name):
        stored = dict(c.execute(select(legacy.c.poi_id, legacy.c.payload).where(legacy.c.dataset_version == v)).all())
    else: stored = {}
    if require_legacy or (inspect(c).has_table(dated.name) and c.execute(select(dated.c.poi_id).where(dated.c.dataset_version == v)).first()):
        stored_dated = dict(c.execute(select(dated.c.poi_id, dated.c.payload).where(dated.c.dataset_version == v)).all())
        if stored_dated != expected_dated(): raise ValueError("existing dataset dated hours mismatch")
    if (require_legacy or stored) and stored != weekly:
        raise ValueError("existing dataset legacy hours mismatch")

def unique_active(db):
    versions = db.scalars(select(DatasetVersion.version).where(DatasetVersion.status == "ACTIVE")).all()
    if len(versions) > 1:
        raise ValueError("multiple active datasets; explicit database repair required")
    return versions[0] if versions else None

def initialize(db, *, activate=False, expected_active=None):
    spec, payload, pois, weekly = expected()
    v = spec["dataset_version"]
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('travelsync-enrichment-snapshot'))"))
    head = unique_active(db)
    if head != expected_active:
        raise ValueError(f"active dataset mismatch: expected {expected_active!r}, found {head!r}; explicit upgrade/rollback required")
    from sqlalchemy import inspect
    installed = (db.execute(select(installations.c.payload).where(installations.c.dataset_version == v)).scalar_one_or_none()
                 if inspect(db.connection()).has_table(installations.name) else None)
    if installed is not None and installed != spec:
        raise ValueError("installed deployment specification/version mismatch")
    if db.get(DatasetVersion, v):
        validate(db, spec, payload, pois, weekly, require_legacy=installed is not None)
    else:
        db.add(DatasetVersion(version=v, status="DRAFT", feature_schema_version="fs-1.0", mapping_version="rule_v2", poi_count=len(pois), description="Packaged enriched deployment snapshot"))
        db.flush()
        db.add_all(pois)
        db.flush()
        schema.create_all(db.connection())
        for e in payload["evidence"]:
            db.execute(evidence.insert().values(dataset_version=v, poi_id=e["place_id"], evidence_index=e["evidence_index"], payload=e))
        for r in payload["review"]:
            db.execute(reviews.insert().values(dataset_version=v, poi_id=r["place_id"], payload=r))
        c = db.connection()
        c.execute(text("CREATE TABLE IF NOT EXISTS poi_hours_import_batches (batch_id VARCHAR(80) PRIMARY KEY,dataset_version VARCHAR(40) NOT NULL,payload_sha256 VARCHAR(64) NOT NULL,poi_count INTEGER NOT NULL,imported_at VARCHAR(64) NOT NULL)"))
        c.execute(text("CREATE TABLE IF NOT EXISTS poi_normalized_hours (dataset_version VARCHAR(40) NOT NULL,poi_id VARCHAR(64) NOT NULL,batch_id VARCHAR(80) NOT NULL,schedule JSON NOT NULL,evidence JSON NOT NULL,observed_at VARCHAR(64) NOT NULL,imported_at VARCHAR(64) NOT NULL,PRIMARY KEY(dataset_version,poi_id,batch_id),FOREIGN KEY(poi_id,dataset_version) REFERENCES pois(id,dataset_version),FOREIGN KEY(batch_id) REFERENCES poi_hours_import_batches(batch_id))"))
        from sqlalchemy import Table
        tables = MetaData()
        batches = Table("poi_hours_import_batches", tables, autoload_with=c)
        hours = Table("poi_normalized_hours", tables, autoload_with=c)
        stamp = datetime.now(timezone.utc).isoformat()
        batch = "hours-" + v
        digest = hashlib.sha256(json.dumps(payload["schedules"], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        c.execute(batches.insert().values(batch_id=batch, dataset_version=v, payload_sha256=digest, poi_count=len(payload["schedules"]), imported_at=stamp))
        for r in payload["schedules"]:
            c.execute(hours.insert().values(dataset_version=v, poi_id=r["place_id"], batch_id=batch, schedule=r["schedule"], evidence=r["evidence"], observed_at=r["observed_at"], imported_at=stamp))
    # Explicit adoption of the original local V4 also freezes baseline weekly hours.
    legacy.create(db.connection(), checkfirst=True)
    exists = db.execute(select(legacy.c.poi_id).where(legacy.c.dataset_version == v)).first()
    if not exists:
        db.execute(legacy.insert(), [dict(dataset_version=v, poi_id=pid, payload=rows) for pid,rows in weekly.items()])
    dated.create(db.connection(), checkfirst=True)
    if not db.execute(select(dated.c.poi_id).where(dated.c.dataset_version == v)).first():
        db.execute(dated.insert(), [dict(dataset_version=v,poi_id=pid,payload=rows) for pid,rows in expected_dated().items()])
    from .seed import HOLIDAYS_2026
    from .models import PublicHoliday
    for day, name in HOLIDAYS_2026 if head is None else []:
        if not db.get(PublicHoliday, day): db.add(PublicHoliday(day=day,name=name))
    db.flush()
    validate(db, spec, payload, pois, weekly, require_legacy=True)
    installations.create(db.connection(), checkfirst=True)
    if installed is None:
        db.execute(installations.insert().values(dataset_version=v,payload=spec))
    if activate and head != v:
        db.execute(update(DatasetVersion).where(DatasetVersion.status == "ACTIVE").values(status="ARCHIVED"))
        target = db.get(DatasetVersion, v)
        target.status, target.activated_at = "ACTIVE", datetime.now(timezone.utc)
    db.commit()
    return {"dataset_version":v, "active_dataset":active_version(db), "metadata_version":v, "hours_batch":"hours-"+v, "normalized_schedules":sum(r["schedule"]["schema_version"] == "normalized-hours-1.0" for r in payload["schedules"]), "provisional_schedules":sum(r["schedule"]["schema_version"] == "provisional-hours-1.0" for r in payload["schedules"])}

def check_startup(db):
    spec, payload, pois, weekly = expected()
    if unique_active(db) != spec["dataset_version"]:
        raise ValueError("active dataset differs from deployment configuration; run explicit setup/upgrade")
    from sqlalchemy import inspect
    if not inspect(db.connection()).has_table(installations.name):
        raise ValueError("snapshot requires explicit installation/adoption")
    installed = db.execute(select(installations.c.payload).where(installations.c.dataset_version == spec["dataset_version"])).scalar_one_or_none()
    if installed != spec:
        raise ValueError("installed deployment specification/version mismatch")
    validate(db, spec, payload, pois, weekly, require_legacy=True)
    return 0

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--activate", action="store_true")
    ap.add_argument("--expected-active", default=None, help="current active version; omit only for an empty database")
    a = ap.parse_args()
    from .db import Base, engine, ensure_enriched_metadata_column
    Base.metadata.create_all(engine)
    ensure_enriched_metadata_column()
    with Session(engine) as db:
        print(json.dumps(initialize(db, activate=a.activate, expected_active=a.expected_active)))
