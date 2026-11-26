"""Read immutable, dataset-scoped normalized schedules without changing legacy hours."""
import json
from datetime import date
from sqlalchemy import inspect,text,bindparam

def validate_schedule(schedule):
 if schedule.get('schema_version')=='provisional-hours-1.0':
  try:from .provisional_schedules import validate_provisional
  except ImportError:from provisional_schedules import validate_provisional
  return validate_provisional(schedule)
 if schedule.get('schema_version')!='normalized-hours-1.0':raise ValueError('unsupported schedule schema')
 date.fromisoformat(schedule['effective_from'])
 weekly=schedule['weekly']
 if set(weekly)!=set(map(str,range(7))):raise ValueError('incomplete week')
 total=0
 for windows in weekly.values():
  previous=-1
  for window in windows:
   if len(window)!=2 or any(type(v) is not int for v in window):raise ValueError('invalid window')
   start,end=window
   if not 0<=start<end<=1440 or start<previous:raise ValueError('overlapping or invalid window')
   previous=end;total+=1
 if not total:raise ValueError('week has no opening windows')
 if schedule.get('independently_verified') is not False:raise ValueError('source-based schedule must retain verification limit')
 return schedule

def load_normalized(connection,version,ids):
 if not ids or not inspect(connection).has_table('poi_normalized_hours'):return {}
 q=text('SELECT poi_id,schedule,evidence,batch_id,observed_at,imported_at FROM poi_normalized_hours WHERE dataset_version=:version AND poi_id IN :ids ORDER BY imported_at DESC,batch_id DESC').bindparams(bindparam('ids',expanding=True))
 out={}
 for raw in connection.execute(q,{'version':version,'ids':list(ids)}).mappings():
  row=dict(raw)
  for k in ['schedule','evidence']:
   if isinstance(row[k],str):row[k]=json.loads(row[k])
  validate_schedule(row['schedule']);out.setdefault(row['poi_id'],row)
 return out

def day_windows(record,day):
 """None means before this import's effective date. [] means explicitly closed."""
 s=record['schedule'];day=date.fromisoformat(day) if isinstance(day,str) else day
 if day<date.fromisoformat(s['effective_from']):return None
 return s['weekly'][str((day.weekday()+1)%7)]

def hhmm(minutes):return f'{minutes//60:02d}:{minutes%60:02d}'


def load_versioned_weekly(connection, version, ids):
    """None: historical version without frozen baseline; dict: version owns its weekly facts."""
    if not inspect(connection).has_table('poi_versioned_legacy_hours'):
        return None
    if not connection.execute(text('SELECT 1 FROM poi_versioned_legacy_hours WHERE dataset_version=:v LIMIT 1'), {'v':version}).first():
        return None
    query = text('SELECT poi_id,payload FROM poi_versioned_legacy_hours WHERE dataset_version=:v AND poi_id IN :ids').bindparams(bindparam('ids', expanding=True))
    result = {}
    for row in connection.execute(query, {'v':version, 'ids':list(ids)}).mappings():
        value = row['payload']
        result[row['poi_id']] = json.loads(value) if isinstance(value,str) else value
    return result


def dataset_diagnostics(connection):
    versions = [dict(r) for r in connection.execute(text('SELECT version,status,poi_count FROM dataset_versions ORDER BY version')).mappings()]
    result = {'datasets':versions, 'active_dataset':[r['version'] for r in versions if r['status']=='ACTIVE']}
    for table in ('poi_metadata','poi_enrichment_evidence','poi_enrichment_reviews','poi_versioned_legacy_hours','poi_versioned_dated_hours','poi_normalized_hours'):
        result[table] = ([dict(r) for r in connection.execute(text(f'SELECT dataset_version,count(*) AS records FROM {table} GROUP BY dataset_version')).mappings()]
                         if inspect(connection).has_table(table) else [])
    result['hours_batches'] = ([dict(r) for r in connection.execute(text('SELECT batch_id,dataset_version,poi_count,payload_sha256 FROM poi_hours_import_batches')).mappings()]
                               if inspect(connection).has_table('poi_hours_import_batches') else [])
    return result


def load_versioned_dated(connection, version, ids):
    if not inspect(connection).has_table('poi_versioned_dated_hours'):
        return []
    query = text('SELECT payload FROM poi_versioned_dated_hours WHERE dataset_version=:v AND poi_id IN :ids').bindparams(bindparam('ids', expanding=True))
    result = []
    for row in connection.execute(query, {'v':version, 'ids':list(ids)}).mappings():
        payload = row['payload']
        for item in json.loads(payload) if isinstance(payload,str) else payload:
            result.append({**item, 'day':date.fromisoformat(item['day'])})
    return result
