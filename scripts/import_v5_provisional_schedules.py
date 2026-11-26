"""Explicit, backed-up in-place amendment of V5's 334 preliminary schedules."""
import collections,csv,gzip,hashlib,json,sys,subprocess
from pathlib import Path
from datetime import datetime,timezone
from sqlalchemy import text,Table,MetaData,select
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from scripts.import_normalized_hours import local_engine
from ourney_travelsync.provisional_schedules import provisional
from ourney_travelsync.operating_schedules import validate_schedule
VERSION='busan-enriched-v5-20261125'
def digest(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
def main():
 out=ROOT/'output/v5_provisional_schedules_20261126';out.mkdir(exist_ok=True)
 manifest_path=ROOT/'src/ourney_travelsync/data/enrichment_v5_manifest.json.gz';spec_path=manifest_path.parent/'deployment_snapshot_v5.json'
 old_bytes=manifest_path.read_bytes();old_spec_bytes=spec_path.read_bytes();m=json.loads(gzip.decompress(old_bytes));spec=json.loads(old_spec_bytes)
 if m['dataset_version']!=VERSION or len(m['payload']['schedules'])!=73:raise ValueError('V5 was already amended or differs from expected input')
 p=m['payload'];grouped=collections.defaultdict(list)
 for e in p['evidence']:
  if e.get('evidence',{}).get('import_status')=='IMPORTED_PRELIMINARY':grouped[e['place_id']].append(e['evidence'])
 if len(grouped)!=334:raise ValueError('preliminary cohort differs from 334')
 if set(grouped)&set(p['suppress_legacy_hours_ids']):raise ValueError('conflicted POI in requested cohort')
 with (manifest_path.parent/spec['csv']).open(encoding='utf-8-sig',newline='') as f:rows={r['place_id']:r for r in csv.DictReader(f)}
 new=[]
 for pid,es in sorted(grouped.items()):
  b=rows[pid];s=provisional(es,p['metadata'][pid],'2026-11-26');validate_schedule(s)
  new.append({'place_id':pid,'place_name':b['place_name'],'latitude':float(b['latitude']),'longitude':float(b['longitude']),
              'schedule':s,'evidence':es,'observed_at':min(e['fetched_at_utc'] for e in es)})
 p['schedules']=sorted(p['schedules']+new,key=lambda r:r['place_id'])
 for e in p['evidence']:
  if e['place_id'] in grouped:e['provisional_schedule_imported']=True
 expandable=sum(any(w is not None for w in r['schedule']['weekly'].values()) for r in new)
 p['stats'].update(provisional_schedule_records=334,provisional_with_expandable_windows=expandable,provisional_with_unresolved_rules=334-expandable,total_schedule_records=407)
 m['in_place_schedule_amendment']={'date':'2026-11-26','user_authorized':True,'previous_manifest_sha256':hashlib.sha256(old_bytes).hexdigest(),'new_dataset_version':False,'provisional_schedule_records':334}
 new_bytes=gzip.compress(json.dumps(m,ensure_ascii=False,sort_keys=True,indent=2).encode(),mtime=0)
 spec['sha256'][manifest_path.name]=hashlib.sha256(new_bytes).hexdigest()
 new_spec_bytes=json.dumps(spec,ensure_ascii=False,indent=2).encode()
 for name,content in [('enrichment_v5_before.json.gz',old_bytes),('deployment_snapshot_v5_before.json',old_spec_bytes)]:
  q=out/name
  if q.exists() and q.read_bytes()!=content:raise ValueError('backup collision')
  q.write_bytes(content)
 backup=out/'database_before.dump'
 if not backup.exists():
  with backup.open('wb') as f:subprocess.run(['docker','exec','ourney_travelsync-db-1','sh','-c','pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl'],stdout=f,check=True)
 engine=local_engine()
 try:
  with engine.begin() as c:
   c.execute(text("SELECT pg_advisory_xact_lock(hashtext('travelsync-enrichment-snapshot'))"))
   active=c.execute(text("SELECT version FROM dataset_versions WHERE status='ACTIVE' FOR UPDATE")).scalars().all()
   if active!=[VERSION]:raise ValueError('V5 is not the unique active dataset')
   baseline={t:digest([dict(r) for r in c.execute(text('SELECT * FROM '+t+' ORDER BY 1,2')).mappings()]) for t in ['pois','poi_metadata','dataset_versions','recommendation_runs','itineraries','poi_enrichment_reviews','poi_versioned_legacy_hours','poi_versioned_dated_hours']}
   original=[dict(r) for r in c.execute(text('SELECT * FROM poi_normalized_hours WHERE dataset_version=:v'),{'v':VERSION}).mappings()]
   if len(original)!=73:raise ValueError('unexpected V5 schedule count')
   schema=MetaData();hours=Table('poi_normalized_hours',schema,autoload_with=c);ev=Table('poi_enrichment_evidence',schema,autoload_with=c);install=Table('poi_deployment_installations',schema,autoload_with=c)
   installed=c.execute(select(install.c.payload).where(install.c.dataset_version==VERSION)).scalar_one()
   if installed!=json.loads(old_spec_bytes):raise ValueError('installed V5 spec differs from packaged V5')
   stamp=datetime.now(timezone.utc).isoformat();batch='hours-'+VERSION
   for r in new:c.execute(hours.insert().values(dataset_version=VERSION,poi_id=r['place_id'],batch_id=batch,schedule=r['schedule'],evidence=r['evidence'],observed_at=r['observed_at'],imported_at=stamp))
   for e in p['evidence']:
    if e['place_id'] in grouped:c.execute(ev.update().where(ev.c.dataset_version==VERSION,ev.c.poi_id==e['place_id'],ev.c.evidence_index==e['evidence_index']).values(payload=e))
   c.execute(text('UPDATE poi_hours_import_batches SET poi_count=:n,payload_sha256=:h WHERE batch_id=:b AND dataset_version=:v'),{'n':407,'h':digest(p['schedules']),'b':batch,'v':VERSION})
   c.execute(install.update().where(install.c.dataset_version==VERSION).values(payload=spec))
   found={r['poi_id']:r['schedule'] for r in c.execute(select(hours).where(hours.c.dataset_version==VERSION)).mappings()}
   if found!={r['place_id']:r['schedule'] for r in p['schedules']}:raise ValueError('schedule readback mismatch')
   for t,h in baseline.items():
    if digest([dict(r) for r in c.execute(text('SELECT * FROM '+t+' ORDER BY 1,2')).mappings()])!=h:raise ValueError('unrelated table changed: '+t)
   # Existing normalized records remain byte-equivalent JSON; metadata is untouched.
   for r in original:
    if found[r['poi_id']]!=r['schedule']:raise ValueError('prior normalized schedule altered')
   try:
    manifest_path.write_bytes(new_bytes);spec_path.write_bytes(new_spec_bytes)
   except BaseException:
    manifest_path.write_bytes(old_bytes);spec_path.write_bytes(old_spec_bytes);raise
 except BaseException:
  manifest_path.write_bytes(old_bytes);spec_path.write_bytes(old_spec_bytes);raise
 result={'dataset_version':VERSION,'new_dataset_version_created':False,'imported_provisional_schedules':334,'existing_normalized_schedules':73,
         'total_schedule_records':407,'expandable_provisional_schedules':expandable,'unresolved_provisional_schedules':334-expandable,
         'metadata_and_old_runs_unchanged':True,'review_records_preserved':True,'manifest_sha256':spec['sha256'][manifest_path.name],
         'backup':str(backup.relative_to(ROOT))}
 (out/'import_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
