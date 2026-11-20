"""Import a validated immutable hours batch into the local Compose database.

No legacy hours, exceptions, dataset versions, metadata, or planning settings are changed.
"""
import argparse,json,hashlib,sys,os,subprocess
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sqlalchemy import create_engine,text
from sqlalchemy.engine import URL
from ourney_travelsync.operating_schedules import validate_schedule,load_normalized
ROOT=Path(__file__).resolve().parents[1]

def apply_batch(engine,manifest,expected_count=3570):
 if manifest.get('schema_version')!='normalized-hours-batch-1.0':raise ValueError('invalid batch schema')
 records=manifest['records'];version=manifest['dataset_version'];batch=manifest['batch_id']
 if not records or len({r['place_id'] for r in records})!=len(records):raise ValueError('empty/duplicate batch')
 for r in records:
  validate_schedule(r['schedule'])
  if not r.get('evidence') or any(not e.get('source_endpoint') or not e.get('fetched_at_utc') for e in r['evidence']):raise ValueError('provenance missing')
 canonical=json.dumps(manifest,ensure_ascii=False,sort_keys=True);digest=hashlib.sha256(canonical.encode()).hexdigest()
 with engine.begin() as c:
  pg=c.dialect.name=='postgresql'
  if pg:c.execute(text("SELECT pg_advisory_xact_lock(hashtext('travelsync-normalized-hours'))"))
  active=c.execute(text("SELECT version FROM dataset_versions WHERE status='ACTIVE'"+(' FOR UPDATE' if pg else ''))).scalars().all()
  if active!=[version]:raise ValueError('target is not the unique active dataset')
  all_pois={r['id']:dict(r) for r in c.execute(text('SELECT id,name,lat,lng FROM pois WHERE dataset_version=:v'),{'v':version}).mappings()}
  if len(all_pois)!=expected_count:raise ValueError('unexpected target POI count')
  for r in records:
   p=all_pois.get(r['place_id'])
   if not p or p['name']!=r['place_name'] or abs(p['lat']-r['latitude'])>1e-6 or abs(p['lng']-r['longitude'])>1e-6:raise ValueError('target POI identity mismatch')
  c.execute(text('CREATE TABLE IF NOT EXISTS poi_hours_import_batches (batch_id VARCHAR(80) PRIMARY KEY,dataset_version VARCHAR(40) NOT NULL,payload_sha256 VARCHAR(64) NOT NULL,poi_count INTEGER NOT NULL,imported_at VARCHAR(64) NOT NULL)'))
  c.execute(text('CREATE TABLE IF NOT EXISTS poi_normalized_hours (dataset_version VARCHAR(40) NOT NULL,poi_id VARCHAR(64) NOT NULL,batch_id VARCHAR(80) NOT NULL,schedule JSON NOT NULL,evidence JSON NOT NULL,observed_at VARCHAR(64) NOT NULL,imported_at VARCHAR(64) NOT NULL,PRIMARY KEY(dataset_version,poi_id,batch_id),FOREIGN KEY(poi_id,dataset_version) REFERENCES pois(id,dataset_version),FOREIGN KEY(batch_id) REFERENCES poi_hours_import_batches(batch_id))'))
  existing=c.execute(text('SELECT payload_sha256,poi_count FROM poi_hours_import_batches WHERE batch_id=:b'),{'b':batch}).mappings().first()
  if existing:
   if existing['payload_sha256']!=digest or existing['poi_count']!=len(records):raise ValueError('batch id already has different content')
   stored=c.execute(text('SELECT count(*) FROM poi_normalized_hours WHERE batch_id=:b'),{'b':batch}).scalar_one()
   if stored!=len(records):raise ValueError('incomplete existing batch')
   return {'batch_id':batch,'dataset_version':version,'imported_pois':0,'already_present_pois':stored,'idempotent':True}
  stamp=datetime.now(timezone.utc).isoformat()
  c.execute(text('INSERT INTO poi_hours_import_batches(batch_id,dataset_version,payload_sha256,poi_count,imported_at) VALUES(:b,:v,:h,:n,:t)'),{'b':batch,'v':version,'h':digest,'n':len(records),'t':stamp})
  js=lambda name:f'CAST(:{name} AS JSON)' if pg else ':'+name
  query=text(f'INSERT INTO poi_normalized_hours(dataset_version,poi_id,batch_id,schedule,evidence,observed_at,imported_at) VALUES(:v,:p,:b,{js("schedule")},{js("evidence")},:o,:t)')
  for r in records:c.execute(query,{'v':version,'p':r['place_id'],'b':batch,'schedule':json.dumps(r['schedule'],ensure_ascii=False),'evidence':json.dumps(r['evidence'],ensure_ascii=False),'o':r['observed_at'],'t':stamp})
  stored=load_normalized(c,version,[r['place_id'] for r in records])
  if any(stored[r['place_id']]['schedule']!=r['schedule'] for r in records):raise ValueError('database readback mismatch')
 return {'batch_id':batch,'dataset_version':version,'imported_pois':len(records),'already_present_pois':0,'idempotent':False}

def local_engine():
 from dotenv import dotenv_values
 env={**dotenv_values(ROOT/'.env'),**os.environ}
 return create_engine(URL.create('postgresql+psycopg',username=env.get('POSTGRES_USER') or 'travelsync',password=env.get('POSTGRES_PASSWORD') or 'travelsync',host='127.0.0.1',port=5432,database=env.get('POSTGRES_DB') or 'travelsync'))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('manifest',type=Path);ap.add_argument('--apply-local',action='store_true');a=ap.parse_args()
 a.manifest=a.manifest.resolve()
 manifest=json.loads(a.manifest.read_text(encoding='utf-8'))
 for rel,digest in manifest['source_sha256'].items():
  if hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()!=digest:raise ValueError('source changed since normalization')
 if not a.apply_local:print(json.dumps({'validated_manifest_pois':len(manifest['records']),'database_write':False}));return
 backup=a.manifest.parent/'legacy_hours_before_import.sql'
 if not backup.exists():
  with backup.open('wb') as f:
   subprocess.run(['docker','exec','ourney_travelsync-db-1','sh','-c','pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl -t public.dataset_versions -t public.poi_hours -t public.poi_exceptions'],stdout=f,check=True)
 engine=local_engine()
 with engine.connect() as c:
  before={table:c.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() for table in ['poi_hours','poi_exceptions','pois']}
 result=apply_batch(engine,manifest)
 with engine.connect() as c:
  after={table:c.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() for table in before}
 if before!=after:raise AssertionError('legacy row counts changed')
 result.update(legacy_counts_unchanged=True,legacy_counts=after,backup=str(backup.relative_to(ROOT)))
 (a.manifest.parent/'import_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
