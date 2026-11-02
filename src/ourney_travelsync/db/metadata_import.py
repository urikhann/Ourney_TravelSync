"""Strict, atomic sidecar import into an explicitly identified POI version."""
import csv
import hashlib
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from ..data_schema import COLUMNS, ENRICHED_COLUMNS


def read_metadata_csv(path, expected_count=3570):
    path = Path(path)
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not {'place_id', 'dataset_version'} <= set(fields):
            raise ValueError('INVALID_METADATA_HEADER')
        if set(fields) - set(COLUMNS) - set(ENRICHED_COLUMNS) - {'dataset_version', 'activity_type'}:
            raise ValueError('UNEXPECTED_METADATA_COLUMNS')
        rows = list(reader)
    if len(rows) != expected_count:
        raise ValueError('METADATA_ROW_COUNT_MISMATCH')
    seen, versions = set(), set()
    for row in rows:
        if None in row or any(v is None for v in row.values()):
            raise ValueError('CSV_ROW_WIDTH_MISMATCH')
        pid = row['place_id']
        if not pid or pid != pid.strip():
            raise ValueError('INVALID_METADATA_PLACE_ID')
        version = str(UUID(row['dataset_version']))
        key = (pid, version)
        if key in seen:
            raise ValueError('DUPLICATE_METADATA_KEY')
        seen.add(key); versions.add(version)
        row['dataset_version'] = version
    if len(versions) != 1:
        raise ValueError('METADATA_REQUIRES_ONE_DATASET_VERSION')
    return rows, hashlib.sha256(path.read_bytes()).hexdigest()


def metadata_payload(row):
    return {k: v for k, v in row.items() if k not in COLUMNS and k != 'dataset_version'}


def validate_matches(rows, pois, version):
    """Validate IDs and supplied core evidence, not just catalogue size."""
    version = str(version)
    if any(r['dataset_version'] != version for r in rows):
        raise ValueError('METADATA_DATASET_VERSION_MISMATCH')
    indexed = {r['place_id']: r for r in pois}
    if len(indexed) != len(pois) or set(indexed) != {r['place_id'] for r in rows}:
        raise ValueError('METADATA_POI_SET_MISMATCH')
    for row in rows:
        poi = indexed[row['place_id']]
        for field in set(COLUMNS) & set(row) & set(poi):
            a = row[field]
            b = poi.get('feature_rules_raw') if field == 'feature_rules' else poi[field]
            if field in ('latitude', 'longitude'):
                equal = abs(Decimal(a) - Decimal(str(b))) <= Decimal('0.00000005')
            else:
                equal = str(a or '') == str(b if b is not None else '')
            if not equal:
                raise ValueError(f'METADATA_CORE_MISMATCH field={field}')


def import_metadata(conn, path, dataset_version=None, *, validate_only=False, expected_count=3570):
    # CSV validation also runs in the core installation without PostgreSQL extras.
    from psycopg.types.json import Jsonb

    rows, checksum = read_metadata_csv(path, expected_count)
    version = str(dataset_version or rows[0]['dataset_version'])
    with conn.transaction():
        dataset = conn.execute('SELECT dataset_version FROM dataset_versions WHERE dataset_version=%s FOR UPDATE',
                               (version,)).fetchone()
        if not dataset:
            raise ValueError('METADATA_DATASET_NOT_FOUND')
        pois = conn.execute('SELECT * FROM poi WHERE dataset_version=%s', (version,)).fetchall()
        validate_matches(rows, pois, version)
        existing = {r['place_id']: r['metadata'] for r in conn.execute(
            'SELECT place_id,metadata FROM poi_metadata WHERE dataset_version=%s', (version,))}
        pending = []
        for row in rows:
            payload = metadata_payload(row)
            if row['place_id'] in existing:
                if existing[row['place_id']] != payload:
                    raise ValueError('METADATA_IMMUTABLE: create a new dataset snapshot')
            else:
                pending.append((row['place_id'], version, Jsonb(payload)))
        if pending and conn.execute('SELECT 1 FROM recommendation_runs WHERE dataset_version=%s LIMIT 1',
                                    (version,)).fetchone():
            raise ValueError('METADATA_RUN_EXISTS: create a new dataset snapshot')
        if not validate_only:
            with conn.cursor() as cursor:
                cursor.executemany('INSERT INTO poi_metadata(place_id,dataset_version,metadata) VALUES(%s,%s,%s)', pending)
        return dict(dataset_version=version, matched_rows=len(rows), existing_rows=len(existing),
                    imported_rows=0 if validate_only else len(pending), validated_only=validate_only, input_sha256=checksum)
