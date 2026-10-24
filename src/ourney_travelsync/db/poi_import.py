"""Strict final-output import. No API calls and no reclassification of merged features."""
import csv
import hashlib
import json
import math
import re
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL
from decimal import Decimal, InvalidOperation
from psycopg import sql, Error
from .connection import safe_db_error
from psycopg.types.json import Jsonb
from ..data_schema import BASE, EXTRA, FEATURES

PIPELINE = Path(__file__).resolve().parents[1] / 'ingestion/travelsync_pipeline_rule_v2.py'
SCHEMA_VERSION = 'travel10-v1'
MAPPING_VERSION = 'rule-v2-' + hashlib.sha256(PIPELINE.read_bytes()).hexdigest()[:16]
COLUMNS = BASE + EXTRA + FEATURES


def validate_rows(rows):
    result, seen = [], set()
    for line, raw in enumerate(rows, 2):
        def fail(field, reason):
            raise ValueError(f'row={line} field={field} {reason}')
        if set(raw) != set(COLUMNS):
            fail('header', 'COLUMN_MISMATCH')
        r = dict(raw)
        for k in BASE + EXTRA:
            v = r[k]
            r[k] = None if v is None or str(v) == r'\N' else str(v)
        for k in ('place_id', 'place_name', 'source', 'source_place_id'):
            if not r[k] or not r[k].strip(): fail(k, 'REQUIRED')
        pid = r['place_id']
        if pid != pid.strip(): fail('place_id', 'WHITESPACE')
        if pid in seen: fail('place_id', 'DUPLICATE')
        seen.add(pid)
        if r['source'] not in ('TOUR_API', 'KAKAO', 'TOUR_API+KAKAO'): fail('source', 'INVALID')
        prefix = 'kakao:' if r['source'] == 'KAKAO' else 'tour:'
        if pid != prefix + r['source_place_id']: fail('source_place_id', 'ID_MISMATCH')
        if not any(re.match(r'^부산(?:광역시)?\s', (r[k] or '').strip()) for k in ('address', 'road_address')):
            fail('address', 'BUSAN_ADDRESS_REQUIRED')
        for k, low, high in [('latitude',32,39), ('longitude',124,132)]:
            try: value = Decimal(r[k] or '')
            except (InvalidOperation, ValueError): fail(k, 'INVALID_NUMBER')
            if not value.is_finite() or not low <= value <= high: fail(k, 'OUT_OF_RANGE')
            r[k] = value
        for k in FEATURES:
            # Reject 1.2, missing, True, etc.; DataFrame numpy integer values become '1'.
            if str(raw[k]) not in ('0','1'): fail(k, 'BINARY_REQUIRED')
            r[k] = int(raw[k])
        if not sum(r[k] for k in FEATURES): fail('features', 'ALL_ZERO')
        if r['exclude_reason'] not in ('', None): fail('exclude_reason', 'EXCLUDED_ROW')
        ids = (r['kakao_place_ids'] or '').strip()
        if r['source'] in ('KAKAO','TOUR_API+KAKAO') and not ids: fail('kakao_place_ids','REQUIRED')
        if ids and not all(re.fullmatch(r'\d+', x.strip()) for x in ids.split(';')): fail('kakao_place_ids','INVALID_IDS')
        if r['source']=='KAKAO' and ids!=r['source_place_id']: fail('kakao_place_ids','ID_MISMATCH')
        result.append(r)
    if not result: raise ValueError('EMPTY_DATASET')
    return result


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or len(reader.fieldnames)!=len(set(reader.fieldnames)):
            raise ValueError('DUPLICATE_OR_MISSING_HEADER')
        if set(reader.fieldnames)!=set(COLUMNS): raise ValueError('COLUMN_MISMATCH: expected pipeline BASE+EXTRA+FEATURES (29)')
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError('CSV_ROW_WIDTH_MISMATCH')
        return validate_rows(rows)


def lock_head(conn):
    # Serializes imports, approvals and head switches even before first head row exists.
    conn.execute('SELECT pg_advisory_xact_lock(84721001)')
    row = conn.execute('SELECT dataset_version FROM dataset_head WHERE singleton').fetchone()
    return row['dataset_version'] if row else None


def register_mapping(conn):
    checksum=hashlib.sha256(PIPELINE.read_bytes()).hexdigest()
    conn.execute('INSERT INTO mapping_versions VALUES(%s,%s,%s,now()) ON CONFLICT DO NOTHING',
                 (MAPPING_VERSION,SCHEMA_VERSION,checksum))
    row=conn.execute('SELECT * FROM mapping_versions WHERE mapping_version=%s',(MAPPING_VERSION,)).fetchone()
    if row['code_sha256']!=checksum or row['feature_schema_version']!=SCHEMA_VERSION:
        raise ValueError('MAPPING_VERSION_CONFLICT')


def begin_dataset(conn,parent,input_hash):
    register_mapping(conn)
    version=uuid4()
    conn.execute('INSERT INTO dataset_versions(dataset_version,parent_version,mapping_version,feature_schema_version,input_sha256) VALUES(%s,%s,%s,%s,%s)',
                 (version,parent,MAPPING_VERSION,SCHEMA_VERSION,input_hash))
    return version


def insert_record(conn, record):
    values={k: Jsonb(v) if isinstance(v,(dict,list)) else v for k,v in record.items()}
    query=sql.SQL('INSERT INTO poi ({}) VALUES ({})').format(
        sql.SQL(',').join(map(sql.Identifier,values)),sql.SQL(',').join(sql.Placeholder()*len(values)))
    conn.execute(query,list(values.values()))


def insert_poi(conn,r,version,submission_id=None):
    record=dict(r)
    pid=uuid5(NAMESPACE_URL,'travelsync:'+r['place_id'])
    record.update(poi_id=pid,dataset_version=version,mapping_version=MAPPING_VERSION,
                  feature_schema_version=SCHEMA_VERSION,submission_id=submission_id,
                  feature_rules_raw=r['feature_rules'],
                  feature_rules={'rules':[x.strip() for x in (r['feature_rules'] or '').split(';') if x.strip()]})
    insert_record(conn,record)
    sources=[]
    if r['source'].startswith('TOUR_API'): sources.append(('TOUR_API',r['source_place_id']))
    if 'KAKAO' in r['source']:
        sources.extend(('KAKAO',x.strip()) for x in r['kakao_place_ids'].split(';'))
    if r['source']=='USER': sources.append(('USER',str(submission_id)))
    for source,sid in sources:
        # Collected timestamp unknown in final CSV; do not claim import time is collection time.
        conn.execute('INSERT INTO poi_source(poi_id,dataset_version,source,source_id,source_url) VALUES(%s,%s,%s,%s,%s)',
                     (pid,version,source,sid,r.get('place_url')))
    return pid


def clone_dataset(conn,parent,version,user_only=False):
    if parent is None:return 0
    meta=conn.execute('SELECT mapping_version FROM dataset_versions WHERE dataset_version=%s',(parent,)).fetchone()
    if meta['mapping_version']!=MAPPING_VERSION: raise ValueError('MAPPING_CHANGED: reprocess source data before combining snapshots')
    rows=conn.execute('SELECT * FROM poi WHERE dataset_version=%s'+(" AND source='USER'" if user_only else ''),(parent,)).fetchall()
    for row in rows:
        row['dataset_version']=version
        insert_record(conn,row)
        conn.execute('INSERT INTO poi_source SELECT poi_id,%s,source,source_id,source_url,collected_at,raw_ref FROM poi_source WHERE dataset_version=%s AND poi_id=%s',
                     (version,parent,row['poi_id']))
    return len(rows)


def finish_dataset(conn,version,activate):
    count=conn.execute('SELECT count(*) AS n FROM poi WHERE dataset_version=%s',(version,)).fetchone()['n']
    conn.execute("UPDATE dataset_versions SET row_count=%s,status='READY' WHERE dataset_version=%s",(count,version))
    if activate:
        conn.execute('INSERT INTO dataset_head VALUES(true,%s) ON CONFLICT(singleton) DO UPDATE SET dataset_version=excluded.dataset_version',(version,))
    return count


def import_rows(conn,rows,*,activate=False,expected_head=None,audit=None):
    rows=validate_rows(rows)
    checksum=hashlib.sha256(json.dumps(rows,default=str,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with conn.transaction():
        parent=lock_head(conn)
        if activate and parent is not None and str(parent)!=str(expected_head):
            raise ValueError('HEAD_CHANGED: provide current --expected-head to explicitly replace active dataset')
        version=begin_dataset(conn,parent,checksum)
        retained=clone_dataset(conn,parent,version,user_only=True)
        for line,r in enumerate(rows,2):
            try:insert_poi(conn,r,version)
            except Error as exc:raise ValueError(f'row={line} '+safe_db_error(exc)) from None
        for detail in audit or []:
            conn.execute('INSERT INTO merge_audit(dataset_version,details) VALUES(%s,%s)',(version,Jsonb(detail)))
        count=finish_dataset(conn,version,activate)
        assert count==len(rows)+retained
        return {'dataset_version':str(version),'imported_rows':len(rows),'retained_user_rows':retained,'total_rows':count,'activated':activate}


def save_pipeline_result(conn,result,*,activate=False,expected_head=None):
    if result['report'].get('complete') is not True:raise ValueError('PARTIAL_RUN_NOT_PUBLISHED')
    # Same final DataFrame and exact merged features, no collection logic changes.
    return import_rows(conn,result['final_places'].to_dict('records'),activate=activate,
                       expected_head=expected_head,audit=json.loads(result['audit'].to_json(orient='records')))


def activate_dataset(conn,version,expected_head=None):
    with conn.transaction():
        current=lock_head(conn)
        if str(current)!=str(expected_head):raise ValueError('HEAD_CHANGED')
        row=conn.execute("SELECT parent_version FROM dataset_versions WHERE dataset_version=%s AND status='READY'",(version,)).fetchone()
        if not row:raise ValueError('DATASET_NOT_READY')
        if row['parent_version']!=current:raise ValueError('STALE_SNAPSHOT: rebuild from current active dataset')
        conn.execute('INSERT INTO dataset_head VALUES(true,%s) ON CONFLICT(singleton) DO UPDATE SET dataset_version=excluded.dataset_version',(version,))
