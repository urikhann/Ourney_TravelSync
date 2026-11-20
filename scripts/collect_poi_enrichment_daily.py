"""Daily resumable POI hours/description collection. No production DB or CSV writes.

Usage: python scripts/collect_poi_enrichment_daily.py [--offline] [--max-new-calls N]
Install collection dependencies with: pip install -e '.[ingestion]'
"""
from __future__ import annotations
import argparse,collections,csv,hashlib,html,json,os,re,sys,time
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import quote,unquote
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.targeted_enrichment import match_record,classify_schedule,text,present
KST=ZoneInfo('Asia/Seoul')
TOUR='https://apis.data.go.kr/B551011/KorService2/'
BUSAN='https://apis.data.go.kr/6260000/'
FIELDS=['food','cafe','nature','history','art','shopping','activity','entertainment','relax','festival']

def now():return datetime.now(timezone.utc)
def atomic_json(path,value):
 tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
 # Windows indexers/editor watchers can briefly hold the destination without delete-sharing.
 # Preserve the existing checkpoint and retry replacement; never fall back to truncating it.
 for attempt in range(8):
  try:tmp.replace(path);return
  except PermissionError:
   if attempt==7:raise
   time.sleep(min(.05*(2**attempt),1))
def read_csv(path):
 with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write_csv(path,rows):
 if not rows:
  path.write_text('place_id\n',encoding='utf-8-sig');return
 with path.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));w.writeheader()
  w.writerows({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','@')) else v) for k,v in r.items()} for r in rows)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def signature(endpoint,params):
 return endpoint+'?'+json.dumps({k:str(v) for k,v in params.items() if k.lower() not in ['servicekey','mobileapp','mobileos']},sort_keys=True,ensure_ascii=False)
def request_day(entry):
 try:return datetime.fromisoformat(entry['fetched_at_utc']).astimezone(KST).date()
 except (KeyError,ValueError,TypeError):return None
def items(payload,tour=True):
 body=payload.get('response',{}).get('body',{}) if tour else next((v for v in payload.values() if isinstance(v,dict)),{})
 value=(body.get('items') or {}).get('item',[]) if tour else body.get('item',[])
 if isinstance(value,dict):value=[value]
 if not isinstance(value,list) or not all(isinstance(x,dict) for x in value):raise ValueError('Invalid items schema')
 return value,body
def description_check(value,name=''):
 value=text(value)
 if not present(value) or len(re.sub(r'\s+','',value))<12:return False
 if value.strip()==name.strip() or re.fullmatch(r'https?://\S+',value):return False
 return not re.fullmatch(r'설명\s*없음|정보\s*없음|미확인|unknown|not found',value,re.I)

@contextmanager
def exclusive_run(path):
 """OS lock automatically releases on process exit, including interrupted runs."""
 f=path.open('a+b');f.seek(0);f.write(b'0');f.flush();f.seek(0)
 try:
  if os.name=='nt':
   import msvcrt
   msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
  else:
   import fcntl
   fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except OSError:
  f.close();raise RuntimeError('Another collection run is active') from None
 try:yield
 finally:f.close()

class Client:
 def __init__(self,out,env,offline=False,max_new_calls=3000,clock=now):
  self.out=out;self.clock=clock;self.offline=offline;self.max_new_calls=max_new_calls;self.new_calls=0
  self.logs=json.loads((out/'requests.json').read_text(encoding='utf-8')) if (out/'requests.json').exists() else []
  self.seeds=[]
  for folder in ['targeted_enrichment_20261117','bulk_hours_20261117']:
   d=ROOT/'output'/folder;p=d/'requests.json'
   if p.exists():self.seeds.extend((d,e) for e in json.loads(p.read_text(encoding='utf-8')))
  self.entries=self.seeds+[(out,e) for e in self.logs]
  self.counts=collections.Counter(e['endpoint'] for _,e in self.entries if request_day(e)==clock().astimezone(KST).date())
  self.cache={}
  for directory,e in self.entries:
   if e.get('status')=='OK' and e.get('response_file'):self.cache[signature(e['endpoint'],e['params'])]=(directory,e)
  self.blocked={};self.last_call=0
  secrets=[v for k,v in env.items() if v and len(v)>=8 and any(s in k.upper() for s in ['KEY','TOKEN','PASSWORD','SECRET'])]
  self.secrets=set(secrets+[unquote(v) for v in secrets]);self.secrets|={quote(v,safe='') for v in self.secrets}
  (out/'responses').mkdir(exist_ok=True)
 def get(self,endpoint,params,source,ttl_days=7):
  sig=signature(endpoint,params);cached=self.cache.get(sig)
  if cached:
   d,e=cached
   try:age=self.clock()-datetime.fromisoformat(e['fetched_at_utc'])
   except (ValueError,TypeError,KeyError):age=timedelta.max
   if age<timedelta(days=ttl_days):
    p=d/e['response_file']
    if sha(p)!=e['response_sha256']:raise ValueError('Cached response checksum mismatch')
    return json.loads(p.read_text(encoding='utf-8')),e
  if self.offline:return None,{'status':'OFFLINE_CACHE_MISS'}
  if endpoint in self.blocked:return None,{'status':self.blocked[endpoint]}
  if self.counts[endpoint]>=1000:self.blocked[endpoint]='DAILY_QUOTA_GUARD';return None,{'status':'DAILY_QUOTA_GUARD'}
  if self.new_calls>=self.max_new_calls:return None,{'status':'RUN_CALL_BUDGET'}
  # Persist a reservation before sending: interruption must not erase quota usage.
  entry={'endpoint':endpoint,'source':source,'params':{k:v for k,v in params.items() if k.lower()!='servicekey'},'fetched_at_utc':self.clock().isoformat(),'status':'RESERVED'}
  self.logs.append(entry);atomic_json(self.out/'requests.json',self.logs)
  self.counts[endpoint]+=1;self.new_calls+=1
  time.sleep(max(0,.25-(time.monotonic()-self.last_call)));self.last_call=time.monotonic()
  payload=None
  try:
   import requests
   response=requests.get(endpoint,params=params,timeout=(8,25));entry['http_status']=response.status_code
   raw=response.text
   for secret in self.secrets:raw=raw.replace(secret,'[REDACTED]')
   p=self.out/'responses'/f'{self.clock().strftime("%Y%m%dT%H%M%S%f")}_{len(self.logs):06}_{source}.txt';p.write_text(raw,encoding='utf-8')
   entry.update(response_file=str(p.relative_to(self.out)),response_sha256=sha(p))
   if response.status_code in [401,403]:entry['status']='AUTHORIZATION_ERROR'
   elif response.status_code==429 or re.search('LIMITED_NUMBER_OF_SERVICE_REQUESTS|DAILY_QUOTA_EXCEEDED',raw,re.I):entry['status']='PROVIDER_QUOTA_EXHAUSTED'
   elif response.status_code!=200:entry['status']='HTTP_ERROR'
   elif re.search('SERVICE_KEY_IS_NOT_REGISTERED|SERVICE_ACCESS_DENIED',raw):entry['status']='AUTHORIZATION_ERROR'
   else:
    payload=json.loads(raw)
    head=payload.get('response',{}).get('header',{}) if endpoint.startswith(TOUR) else next((v for v in payload.values() if isinstance(v,dict)),{}).get('header',{})
    code=str(head.get('resultCode',head.get('code','')));entry['provider_code']=code
    entry['status']='OK' if code in ['0','00','0000'] else 'PROVIDER_QUOTA_EXHAUSTED' if code=='22' else 'PROVIDER_ERROR'
    if entry['status']=='OK':it,b=items(payload,endpoint.startswith(TOUR));entry.update(returned_records=len(it),total_count=b.get('totalCount'))
   if entry['status'] in ['AUTHORIZATION_ERROR','PROVIDER_QUOTA_EXHAUSTED']:self.blocked[endpoint]=entry['status']
   if entry['status']!='OK':payload=None
  except Exception as exc:entry.update(status='REQUEST_OR_SCHEMA_ERROR',error_type=type(exc).__name__)
  atomic_json(self.out/'requests.json',self.logs)
  if payload is not None:self.cache[sig]=(self.out,entry)
  return payload,entry
 def listing(self,endpoint,params,source):
  rows=[];seen=set();total=None
  for page in range(1,101):
   payload,e=self.get(endpoint,{**params,'pageNo':page,'numOfRows':100},source,ttl_days=1)
   if payload is None:return rows,{'complete':False,'retrieved':len(rows),'status':e['status']}
   batch,body=items(payload,endpoint.startswith(TOUR));total=int(body.get('totalCount') or 0)
   for r in batch:
    rid=str(r.get('contentid') or r.get('UC_SEQ'))
    if rid not in seen:seen.add(rid);rows.append({**r,'_retrieved_at_utc':e.get('fetched_at_utc','')})
   if len(rows)>=total or not batch:return rows,{'complete':True,'retrieved':len(rows),'reported_total':total,'status':'OK'}
  return rows,{'complete':False,'retrieved':len(rows),'status':'PAGINATION_LIMIT'}

def run(args):
 out=args.out.resolve()
 data=ROOT/'src/ourney_travelsync/data'
 if out==data.resolve() or data.resolve() in out.parents:raise ValueError('Output cannot be packaged data')
 from dotenv import dotenv_values
 env={**dotenv_values(ROOT/'.env'),**os.environ}
 out.mkdir(parents=True,exist_ok=True)
 with exclusive_run(out/'collection.lock'):
  return collect(args,out,env)
def collect(args,out,env):
 c=Client(out,env,args.offline,args.max_new_calls)
 key=unquote(env.get('TOUR_API_KEY') or env.get('TOUR_KEY') or '')
 if not key and not args.offline:raise RuntimeError('TourAPI key missing from repository .env')
 bkey=unquote(next((env.get(k) for k in ['BUSAN_FOOD_API_KEY','BUSAN_API_KEY','DATA_GO_KR_API_KEY','TOUR_API_KEY','TOUR_KEY'] if env.get(k)),''))
 data=ROOT/'src/ourney_travelsync/data';paths=[data/'travelsync_poi_metadata_enriched_v3.csv',data/'travelsync_poi_operating_info_v1.csv'];hashes={str(p.relative_to(ROOT)):sha(p) for p in paths}
 places=read_csv(paths[0]);assert len({p['place_id'] for p in places})==len(places)
 common={'serviceKey':key,'MobileOS':'ETC','MobileApp':'TravelSyncEnrichmentAudit','_type':'json'}
 tour,ts=c.listing(TOUR+'areaBasedList2',{**common,'lDongRegnCd':26,'arrange':'A'},'tour_list_current')
 food,fs=c.listing(BUSAN+'FoodService/getFoodKr',{'serviceKey':bkey,'resultType':'json'},'busan_food')
 attractions,ats=c.listing(BUSAN+'AttractionService/getAttractionKr',{'serviceKey':bkey,'resultType':'json'},'busan_attraction')
 # Use complete same-day seed catalogues offline even if one unavailable provider has no cache.
 targets={};evidence=[];keywords={};inventory=[]
 def add(p,source,sid,basis,hours='',closure='',description='',stamp='',endpoint=''):
  hrs=text(hours);desc=text(description)
  assessment=classify_schedule(hrs,closure)
  dvalid=description_check(desc,p['place_name'])
  evidence.append({'place_id':p['place_id'],'place_name':p['place_name'],'source':source,'source_id':str(sid),'match_basis':basis,'source_endpoint':endpoint,'fetched_at_utc':stamp,'hours_text':hrs,'closure_text':text(closure),'description_text':desc,'description_content_valid':dvalid,'automatic_validation':'PASS_SOURCE_MATCH_AND_CONTENT' if hrs or dvalid else 'NO_USABLE_CONTENT',**assessment,'verified_for_planning':False,'import_status':'NOT_IMPORTED'})
 for p in places:
  pid=p['place_id']
  for source,records in [('busan_food',food),('busan_attraction',attractions),('tour',tour)]:
   found,basis,distance=match_record(p,records,'tour' if source=='tour' else 'busan')
   if found:
    if source=='tour':targets[pid]=(found,basis)
    else:add(p,source,found['UC_SEQ'],basis,found.get('USAGE_DAY_WEEK_AND_TIME',''),found.get('HLDY_INFO',''),found.get('ITEMCNTNTS',''),found.get('_retrieved_at_utc',''),BUSAN+('FoodService/getFoodKr' if source=='busan_food' else 'AttractionService/getAttractionKr'))
  if pid not in targets and pid.startswith('tour:') and p.get('category','').isdigit():targets[pid]=({'contentid':p['source_place_id'],'contenttypeid':p['category']},'EXACT_EXISTING_SOURCE_ID')
 # Discovered identities are restored without consuming keyword quota. Successful no-match responses
 # stay cached 30 days, allowing subsequent days to reach the remaining catalogue.
 keyword_path=out/'keyword_identities.json'
 if keyword_path.exists():keywords=json.loads(keyword_path.read_text(encoding='utf-8'))
 seed=ROOT/'output/bulk_hours_20261117/keyword_state.json'
 if seed.exists():
  for pid,k in json.loads(seed.read_text(encoding='utf-8')).items():keywords.setdefault(pid,k)
 pby={p['place_id']:p for p in places}
 for pid,k in keywords.items():
  if pid in pby and k.get('record'):targets.setdefault(pid,(k['record'],k['match_status']))
 for index,p in enumerate(places):
  pid=p['place_id']
  if pid in targets:continue
  payload,e=c.get(TOUR+'searchKeyword2',{**common,'lDongRegnCd':26,'keyword':p['place_name'],'numOfRows':100,'pageNo':1},'tour_keyword_full',ttl_days=30)
  if payload is None:continue
  records,body=items(payload);found,basis,d=match_record(p,records,'tour')
  keywords[pid]={'status':'OK','record':found,'match_status':basis,'fetched_at_utc':e.get('fetched_at_utc')}
  if found:targets[pid]=(found,basis)
  if index%25==0:atomic_json(keyword_path,keywords)
 atomic_json(keyword_path,keywords)
 statuses={}
 for index,(pid,(r,basis)) in enumerate(targets.items(),1):
  p=pby[pid];params={**common,'contentId':r['contentid'],'contentTypeId':r.get('contenttypeid') or p.get('category','')}
  payload,e=c.get(TOUR+'detailIntro2',params,'tour_intro_full')
  statuses[pid]={'hours_request':e['status']}
  if payload:
   rr,body=items(payload)
   if rr:
    d=rr[0];hrs=' | '.join(text(d[k]) for k in ['opentimefood','usetime','usetimeculture','opentime','usetimeleports','playtime'] if present(d.get(k)))
    closure=' | '.join(text(d[k]) for k in ['restdatefood','restdate','restdateculture','restdateshopping','restdateleports'] if present(d.get(k)))
    add(p,'tour_intro',r['contentid'],basis,hrs,closure,stamp=e.get('fetched_at_utc',''),endpoint=TOUR+'detailIntro2')
  payload,e=c.get(TOUR+'detailCommon2',{**common,'contentId':r['contentid']},'tour_common_full',ttl_days=30)
  statuses[pid]['description_request']=e['status']
  if payload:
   rr,body=items(payload)
   if rr:
    # Confirm common-information identity when coordinates and title are returned.
    rr[0].setdefault('contentid',r['contentid'])
    found,checked,distance=match_record(p,rr,'tour')
    if found and (distance is None or distance<=200):add(p,'tour_common',r['contentid'],checked,description=found.get('overview',''),stamp=e.get('fetched_at_utc',''),endpoint=TOUR+'detailCommon2')
    else:
     statuses[pid]['description_request']='IDENTITY_RECHECK_FAILED'
     for observed in evidence:
      if observed['place_id']==pid and observed['source']=='tour_intro':observed['automatic_validation']='REVIEW_IDENTITY'
  if index%25==0:
   write_csv(out/'evidence_latest.csv',evidence);print(f'Hours/descriptions: {index}/{len(targets)}',flush=True)
 by=collections.defaultdict(list)
 for e in evidence:by[e['place_id']].append(e)
 for p in places:
  pid=p['place_id'];ev=by[pid];hr=[e for e in ev if e['hours_text']];de=[e for e in ev if e['description_content_valid']]
  conflict=len({e['time_ranges'] for e in hr if e['time_ranges']})>1
  inventory.append({'place_id':pid,'place_name':p['place_name'],'hours_retrieved':bool(hr),'description_retrieved':bool(de),'hours_sources':';'.join(sorted({e['source'] for e in hr})),'description_sources':';'.join(sorted({e['source'] for e in de})),'hours_text':' || '.join(e['hours_text'] for e in hr),'closure_text':' || '.join(e['closure_text'] for e in hr if e['closure_text']),'description_text':max((e['description_text'] for e in de),key=len,default=''),'regular_schedule_candidate':any(e['schedule_evidence_status']=='REGULAR_SCHEDULE_CANDIDATE_REVIEW_REQUIRED' for e in hr),'source_time_range_disagreement':conflict,'automatic_validation':'REVIEW_IDENTITY' if any(e['automatic_validation']=='REVIEW_IDENTITY' for e in ev) else 'REVIEW_SOURCE_DIFFERENCE' if conflict else 'PASS_SOURCE_MATCH_AND_CONTENT' if hr or de else 'NO_USABLE_CONTENT','keyword_state':keywords.get(pid,{}).get('match_status','NOT_REQUIRED' if pid in targets else 'DEFERRED'),'request_statuses':json.dumps(statuses.get(pid,{}),ensure_ascii=False),'verified_for_planning':False,'import_status':'NOT_IMPORTED'})
 previous=json.loads((out/'latest_summary.json').read_text(encoding='utf-8')) if (out/'latest_summary.json').exists() else {}
 validation={'unique_ids':len({r['place_id'] for r in inventory})==len(places),'all_catalogue_rows_saved':len(inventory)==len(places),'source_files_unchanged':all(sha(ROOT/rel)==v for rel,v in hashes.items()),'no_production_import':True}
 if not all(validation.values()):raise AssertionError('Collection validation failed')
 summary={'run_at_utc':now().isoformat(),'quota_day_kst':now().astimezone(KST).date().isoformat(),'offline':args.offline,'catalogue_pois':len(places),'pois_with_hours':sum(r['hours_retrieved'] for r in inventory),'pois_with_descriptions':sum(r['description_retrieved'] for r in inventory),'regular_schedule_candidates':sum(r['regular_schedule_candidate'] for r in inventory),'source_time_range_disagreements':sum(r['source_time_range_disagreement'] for r in inventory),'remaining_keyword_pois':sum(r['keyword_state']=='DEFERRED' for r in inventory),'tour_detail_targets':len(targets),'new_requests_this_run':c.new_calls,'operation_attempts_today':dict(c.counts),'blocked_operations':c.blocked,'source_catalogues':{'tour':ts,'food':fs,'attraction':ats},'validation':validation,'input_sha256':hashes,'imported':0}
 summary['change_since_previous_report']={k:summary[k]-previous.get(k,0) for k in ['pois_with_hours','pois_with_descriptions','regular_schedule_candidates']}
 summary['request_statuses_this_run']=dict(collections.Counter(e['status'] for e in c.logs[-c.new_calls:])) if c.new_calls else {}
 write_csv(out/'all_pois_latest.csv',inventory);write_csv(out/'evidence_latest.csv',evidence)
 write_csv(out/'validated_evidence_latest.csv',[e for e in evidence if e['automatic_validation']=='PASS_SOURCE_MATCH_AND_CONTENT' and not any(r['source_time_range_disagreement'] for r in inventory if r['place_id']==e['place_id'])])
 write_csv(out/'review_queue_latest.csv',[r for r in inventory if r['automatic_validation']!='PASS_SOURCE_MATCH_AND_CONTENT' or r['regular_schedule_candidate']])
 run_dir=out/'runs'/now().astimezone(KST).strftime('%Y%m%d_%H%M%S');run_dir.mkdir(parents=True,exist_ok=True)
 atomic_json(run_dir/'summary.json',summary);atomic_json(out/'latest_summary.json',summary)
 write_csv(run_dir/'all_pois.csv',inventory);write_csv(run_dir/'evidence.csv',evidence)
 report=f"# Daily POI data collection — {summary['quota_day_kst']}\n\nChecked {len(places):,} catalogue POIs. Hours text: {summary['pois_with_hours']:,}; description content: {summary['pois_with_descriptions']:,}; preliminary regular-schedule candidates: {summary['regular_schedule_candidates']:,}.\n\nChanges since previous report: {summary['change_since_previous_report']}. New requests: {c.new_calls}. Deferred keyword searches: {summary['remaining_keyword_pois']}.\n\nSource catalogue states: {summary['source_catalogues']}. Blocked operations: {c.blocked}.\n\nValidation: {validation}. Checks cover schema, conservative source identity, content presence, clock-range screening, source differences, completeness and source-file immutability. They do not prove real-world opening hours, description accuracy or itinerary feasibility.\n\nUncertain hours, seasonal rules, breaks, last order/admission cutoffs and source differences remain reviewable. Raw evidence and retrieval timestamps are preserved. No active dataset, database, parser or enforcement setting was changed.\n\nSee all_pois_latest.csv, evidence_latest.csv, validated_evidence_latest.csv and review_queue_latest.csv.\n"
 (out/'REPORT_latest.md').write_text(report,encoding='utf-8');(run_dir/'REPORT.md').write_text(report,encoding='utf-8')
 print(json.dumps(summary,ensure_ascii=False,indent=2));return summary
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',type=Path,default=ROOT/'output/daily_poi_enrichment');ap.add_argument('--offline',action='store_true');ap.add_argument('--max-new-calls',type=int,default=3000);args=ap.parse_args()
 if not 0<=args.max_new_calls<=3000:ap.error('max-new-calls must be 0..3000; each operation remains capped at 1000')
 run(args)
if __name__=='__main__':main()
