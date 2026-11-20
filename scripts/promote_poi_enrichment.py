"""Explicit, atomic promotion of collected evidence to a new dataset snapshot.

No provider calls. Existing datasets/runs and legacy hours are preserved.
"""
import argparse, collections, copy, csv, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import MetaData, Table, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from scripts.normalize_poi_hours import build, read, clean
from scripts.import_normalized_hours import local_engine
from ourney_travelsync.operating_schedules import validate_schedule

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()

def build_payload(metadata, evidence, inventory, baseline, oldops, effective_from):
    """Gate fields independently: ambiguous hours never become planning facts."""
    meta = copy.deepcopy(metadata)
    inv = {r['place_id']:r for r in inventory}
    base = {r['place_id']:r for r in baseline}
    assert set(meta)==set(inv)==set(base), 'catalogue identity set mismatch'
    schedules, held = build(evidence, inventory, baseline, oldops, effective_from)
    hours = {r['place_id']:r for r in schedules}
    held_map = {r['place_id']:r['reason'] for r in held}
    grouped = collections.defaultdict(list)
    for e in evidence: grouped[e['place_id']].append(e)
    changed, added, suppressed, stored, review = [], [], [], [], []
    for pid in sorted(meta):
        row, m = inv[pid], meta[pid]
        reasons = []
        conflict = row['source_time_range_disagreement']=='True'
        identity = row['automatic_validation']=='REVIEW_IDENTITY' or m.get('detail_verification_status')=='CONFLICT'
        if conflict: reasons.append('SOURCE_RANGE_DISAGREEMENT')
        if identity: reasons.append('IDENTITY_REVIEW_REQUIRED')
        if conflict or identity: suppressed.append(pid)
        desc = [e for e in grouped[pid] if e['description_content_valid']=='True'
                and e['automatic_validation']=='PASS_SOURCE_MATCH_AND_CONTENT' and not (conflict or identity)]
        prior = clean(m.get('description_summary'))
        chunks = [prior] if prior else []
        for e in sorted(desc, key=lambda e:(e['source'], e['source_id'])):
            value = clean(e['description_text'])
            if value and not any(value in chunk for chunk in chunks): chunks.append(value)
        combined = '\n\n'.join(chunks)
        if desc:
            m['collected_description_evidence'] = desc
            m['description_enrichment_status'] = 'SOURCE_MATCHED_NOT_INDEPENDENTLY_VERIFIED'
            if combined != prior:
                changed.append(pid)
                if not prior: added.append(pid)
                m['prior_description_summary'] = prior or None
                m['description_summary'] = combined
                m['description_raw'] = combined
                refs = list(dict.fromkeys([e['source_endpoint']+'?'+('UC_SEQ=' if e['source'].startswith('busan_') else 'contentId=')+e['source_id'] for e in desc]))
                m['description_source_url'] = ' | '.join(filter(None,[clean(m.get('description_source_url')),*refs]))
                m['description_source_type'] = 'OFFICIAL_API_SOURCE_MATCHED'
                m['description_basis'] = 'Existing description retained; matched official API text appended verbatim. Not independently venue-verified.'
                m['description_checked_at'] = max(e['fetched_at_utc'] for e in desc)
        if pid in held_map and held_map[pid] not in reasons: reasons.append(held_map[pid])
        if row['hours_retrieved']!='True': reasons.append('NO_RETRIEVED_HOURS')
        if row['description_retrieved']!='True': reasons.append('NO_RETRIEVED_DESCRIPTION')
        if reasons: review.append({'place_id':pid,'place_name':row['place_name'],'reasons':reasons,'evidence':grouped[pid]})
        for n,e in enumerate(grouped[pid]):
            stored.append({'place_id':pid,'evidence_index':n,'description_usable':e in desc,
                           'hours_usable':pid in hours and bool(clean(e.get('hours_text'))),
                           'review_reasons':reasons,'evidence':e})
    return {'metadata':meta,'schedules':schedules,'held_hours':held,'review':review,'evidence':stored,
            'suppress_legacy_hours_ids':suppressed,
            'stats':{'description_evidence_pois':sum(bool(m.get('collected_description_evidence')) for m in meta.values()),
                     'description_changed_pois':len(changed),'new_description_pois':len(added),
                     'normalized_hours_pois':len(schedules),'held_hours_pois':len(held),
                     'hours_hold_reasons':dict(collections.Counter(r['reason'] for r in held)),
                     'review_pois':len(review),'suppressed_conflict_or_identity_pois':len(suppressed)},
            'description_changed_ids':changed,'new_description_ids':added}

def get_state(c, version):
    schema=MetaData()
    p=Table('pois',schema,autoload_with=c);m=Table('poi_metadata',schema,autoload_with=c)
    rows=[dict(r) for r in c.execute(select(p).where(p.c.dataset_version==version).order_by(p.c.id)).mappings()]
    metadata={r['place_id']:r['metadata'] for r in c.execute(select(m).where(m.c.dataset_version==version)).mappings()}
    return rows,metadata

def fingerprint(c, version):
    rows,meta=get_state(c,version)
    return digest({'pois':rows,'metadata':meta})

def apply_snapshot(engine, manifest):
    """All POIs, metadata, evidence, hours and activation commit together."""
    version=manifest['dataset_version'];previous=manifest['parent_version'];payload=manifest['payload']
    for r in payload['schedules']:validate_schedule(r['schedule'])
    with engine.begin() as c:
        if c.dialect.name=='postgresql':c.execute(text("SELECT pg_advisory_xact_lock(hashtext('travelsync-enrichment-snapshot'))"))
        schema=MetaData()
        dv=Table('dataset_versions',schema,autoload_with=c);p=Table('pois',schema,autoload_with=c);m=Table('poi_metadata',schema,autoload_with=c)
        if c.execute(select(dv.c.version).where(dv.c.version==version)).first():raise ValueError('snapshot already exists; never overwrite')
        active=c.execute(select(dv.c.version).where(dv.c.status=='ACTIVE').with_for_update()).scalars().all()
        if active!=[previous]:raise ValueError('parent is not the unique active dataset')
        if fingerprint(c,previous)!=manifest['parent_database_sha256']:raise ValueError('parent changed after preparation')
        rows,_=get_state(c,previous)
        if len(rows)!=3570 or {r['id'] for r in rows}!=set(payload['metadata']):raise ValueError('POI set mismatch')
        parent=dict(c.execute(select(dv).where(dv.c.version==previous)).mappings().one())
        stamp=datetime.now(timezone.utc).isoformat()
        c.execute(dv.insert().values(version=version,status='DRAFT',feature_schema_version=parent['feature_schema_version'],
                  mapping_version=parent['mapping_version'],poi_count=len(rows),description='Official API enrichment snapshot; uncertain evidence retained for review',created_at=datetime.now(timezone.utc)))
        suppressed=set(payload['suppress_legacy_hours_ids'])
        for row in rows:
            row['dataset_version']=version
            # Clone every core field; only current identity/time conflicts suppress legacy known hours.
            if row['id'] in suppressed:
                row['field_status']={**(row['field_status'] or {}),'hours':'UNKNOWN','closed_days':'UNKNOWN','enrichment_review_required':True}
            c.execute(p.insert().values(**row))
            c.execute(m.insert().values(place_id=row['id'],dataset_version=version,metadata=payload['metadata'][row['id']]))
        c.execute(text('CREATE TABLE IF NOT EXISTS poi_enrichment_evidence (dataset_version VARCHAR(40) NOT NULL,poi_id VARCHAR(64) NOT NULL,evidence_index INTEGER NOT NULL,payload JSON NOT NULL,PRIMARY KEY(dataset_version,poi_id,evidence_index),FOREIGN KEY(poi_id,dataset_version) REFERENCES pois(id,dataset_version))'))
        c.execute(text('CREATE TABLE IF NOT EXISTS poi_enrichment_reviews (dataset_version VARCHAR(40) NOT NULL,poi_id VARCHAR(64) NOT NULL,payload JSON NOT NULL,PRIMARY KEY(dataset_version,poi_id),FOREIGN KEY(poi_id,dataset_version) REFERENCES pois(id,dataset_version))'))
        ev=Table('poi_enrichment_evidence',schema,autoload_with=c);rv=Table('poi_enrichment_reviews',schema,autoload_with=c)
        for e in payload['evidence']:c.execute(ev.insert().values(dataset_version=version,poi_id=e['place_id'],evidence_index=e['evidence_index'],payload=e))
        for r in payload['review']:c.execute(rv.insert().values(dataset_version=version,poi_id=r['place_id'],payload=r))
        # Same schema and reader as the previously verified normalized-hours importer.
        c.execute(text('CREATE TABLE IF NOT EXISTS poi_hours_import_batches (batch_id VARCHAR(80) PRIMARY KEY,dataset_version VARCHAR(40) NOT NULL,payload_sha256 VARCHAR(64) NOT NULL,poi_count INTEGER NOT NULL,imported_at VARCHAR(64) NOT NULL)'))
        c.execute(text('CREATE TABLE IF NOT EXISTS poi_normalized_hours (dataset_version VARCHAR(40) NOT NULL,poi_id VARCHAR(64) NOT NULL,batch_id VARCHAR(80) NOT NULL,schedule JSON NOT NULL,evidence JSON NOT NULL,observed_at VARCHAR(64) NOT NULL,imported_at VARCHAR(64) NOT NULL,PRIMARY KEY(dataset_version,poi_id,batch_id),FOREIGN KEY(poi_id,dataset_version) REFERENCES pois(id,dataset_version),FOREIGN KEY(batch_id) REFERENCES poi_hours_import_batches(batch_id))'))
        batches=Table('poi_hours_import_batches',schema,autoload_with=c);hours=Table('poi_normalized_hours',schema,autoload_with=c)
        batch='hours-'+version
        if payload['schedules']:
            c.execute(batches.insert().values(batch_id=batch,dataset_version=version,payload_sha256=digest(payload['schedules']),poi_count=len(payload['schedules']),imported_at=stamp))
            for r in payload['schedules']:c.execute(hours.insert().values(dataset_version=version,poi_id=r['place_id'],batch_id=batch,schedule=r['schedule'],evidence=r['evidence'],observed_at=r['observed_at'],imported_at=stamp))
        # Readback before activation. Roll back everything on any mismatch.
        _,stored=get_state(c,version)
        if stored!=payload['metadata']:raise ValueError('metadata readback mismatch')
        if fingerprint(c,previous)!=manifest['parent_database_sha256']:raise ValueError('parent dataset changed')
        c.execute(dv.update().where(dv.c.status=='ACTIVE').values(status='ARCHIVED'))
        c.execute(dv.update().where(dv.c.version==version).values(status='ACTIVE',activated_at=datetime.now(timezone.utc)))
    return {'dataset_version':version,'parent_version':previous,'imported_pois':len(rows),'normalized_hours_batch':batch,**payload['stats']}

def write_csv(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--version',default='busan-enriched-v4-20261121');ap.add_argument('--effective-from',default='2026-11-21');ap.add_argument('--apply-local',action='store_true');a=ap.parse_args()
    a.out.mkdir(parents=True,exist_ok=True);path=a.out/'manifest.json';engine=local_engine()
    if a.apply_local:
        manifest=json.loads(path.read_text(encoding='utf-8'))
        for rel,h in manifest['source_sha256'].items():
            if hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()!=h:raise ValueError('source changed')
        result=apply_snapshot(engine,manifest)
        (a.out/'import_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2));return
    if path.exists():raise ValueError('preparation manifest already exists; use a new output directory')
    daily=ROOT/'output/daily_poi_enrichment';data=ROOT/'src/ourney_travelsync/data'
    paths=[daily/'evidence_latest.csv',daily/'all_pois_latest.csv',data/'travelsync_poi_metadata_enriched_v3.csv',data/'travelsync_poi_operating_info_v1.csv']
    with engine.connect() as c:
        previous=c.execute(text("SELECT version FROM dataset_versions WHERE status='ACTIVE'")).scalar_one()
        rows,metadata=get_state(c,previous);parent_hash=fingerprint(c,previous)
    baseline=read(paths[2]);base={r['place_id']:r for r in baseline}
    assert len(rows)==3570
    for r in rows:
        b=base[r['id']]
        assert r['name']==b['place_name'] and abs(r['lat']-float(b['latitude']))<1e-6 and abs(r['lng']-float(b['longitude']))<1e-6
    payload=build_payload(metadata,read(paths[0]),read(paths[1]),baseline,read(paths[3]),a.effective_from)
    manifest={'schema_version':'poi-enrichment-snapshot-1.0','dataset_version':a.version,'parent_version':previous,'parent_database_sha256':parent_hash,
              'effective_from':a.effective_from,'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'payload':payload}
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    write_csv(a.out/'review_queue.csv',[{'place_id':r['place_id'],'place_name':r['place_name'],'reasons':';'.join(r['reasons']),'evidence_json':json.dumps(r['evidence'],ensure_ascii=False)} for r in payload['review']])
    # Separate deployment CSV for the recommender's standalone fallback. Preserve v3 file verbatim.
    exported=[]
    for b in baseline:
        row={**b,'dataset_version':a.version}
        for k,v in payload['metadata'][b['place_id']].items():row[k]=json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v if v is not None else 'NULL'
        exported.append(row)
    fields=list(dict.fromkeys(k for r in exported for k in r));exported=[{k:r.get(k,'NULL') for k in fields} for r in exported]
    write_csv(a.out/'travelsync_poi_metadata_enriched_v4.csv',exported)
    (a.out/'preparation_summary.json').write_text(json.dumps(payload['stats'],ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(payload['stats'],ensure_ascii=False,indent=2))

if __name__=='__main__':main()
