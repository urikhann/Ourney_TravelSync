"""Import a metadata sidecar into an existing, explicitly identified API dataset.

Run from api/: python -m app.import_metadata FILE --version DATASET --validate-only
"""
import argparse
import csv
import hashlib
from pathlib import Path

from sqlalchemy import select

from .import_places import CORE_COLUMNS, ImportProblem
from .metadata import clean_meta
from .models import DatasetVersion, Poi, PoiMetadata, RecommendationRun


def import_metadata(db, path, version, *, validate_only=False, expected_count=3570):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not {'place_id', 'dataset_version'} <= set(fields):
            raise ImportProblem(['invalid metadata header'])
        rows = list(reader)
    if len(rows) != expected_count or any(None in r or any(v is None for v in r.values()) for r in rows):
        raise ImportProblem(['metadata row count or width mismatch'])
    keys = [(r['place_id'], r['dataset_version']) for r in rows]
    if len(keys) != len(set(keys)) or any(not p or p != p.strip() or v != version for p, v in keys):
        raise ImportProblem(['duplicate key or dataset_version mismatch'])
    try:
        dataset = db.scalar(select(DatasetVersion).where(DatasetVersion.version == version).with_for_update())
        if dataset is None:
            raise ImportProblem(['dataset not found'])
        pois = {p.id: p for p in db.scalars(select(Poi).where(Poi.dataset_version == version))}
        if set(pois) != {p for p, _ in keys}:
            raise ImportProblem(['metadata POI set does not match dataset'])
        pending = []
        for row in rows:
            poi = pois[row['place_id']]
            if ('place_name' in row and row['place_name'] != poi.name) or any(
                f in row and str(poi.features.get(f)) != row[f] for f in poi.features):
                raise ImportProblem(['metadata core identity or features mismatch'])
            for field, actual in [('latitude', poi.lat), ('longitude', poi.lng)]:
                if field in row and abs(float(row[field]) - actual) > 0.00000005:
                    raise ImportProblem(['metadata coordinate mismatch'])
            payload = clean_meta({k: v for k, v in row.items() if k not in CORE_COLUMNS})
            if poi.metadata_record is not None:
                if clean_meta(poi.enriched_metadata) != payload:
                    raise ImportProblem(['metadata is immutable; create a new dataset snapshot'])
            else:
                pending.append(PoiMetadata(place_id=poi.id, dataset_version=version, metadata_json=payload))
        if pending and db.scalar(select(RecommendationRun.id).where(RecommendationRun.dataset_version == version).limit(1)):
            raise ImportProblem(['dataset already has recommendation runs; create a new snapshot'])
        if not validate_only:
            db.add_all(pending)
            db.commit()
        else:
            db.rollback()
        return dict(dataset_version=version, matched_rows=len(rows), imported_rows=0 if validate_only else len(pending),
                    validated_only=validate_only, input_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
    except Exception:
        db.rollback()
        raise


if __name__ == '__main__':
    import json
    from .db import Base, SessionLocal, engine, ensure_enriched_metadata_column
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv'); parser.add_argument('--version', required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    Base.metadata.create_all(engine)
    ensure_enriched_metadata_column()
    with SessionLocal() as session:
        print(json.dumps(import_metadata(session, args.csv, args.version, validate_only=args.validate_only)))
