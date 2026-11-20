"""Normalize collected official-source hours; no database writes.

Accept complete explicit recurring schedules that fit the current planner contract.
Complex/conditional/admission/overnight schedules remain in a review queue.
"""
import argparse,csv,json,re,hashlib,sys,collections
from pathlib import Path
from datetime import date,datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ourney_travelsync.operating_schedules import validate_schedule
ROOT=Path(__file__).resolve().parents[1]
DOW={v:i for i,v in enumerate('일월화수목금토')}
RANGE=re.compile(r'(?<!\d)(\d{1,2}):(\d{2})\s*[~～–—-]\s*(\d{1,2}):(\d{2})(?!\d)')
COMPLEX=re.compile(r'계절|동절기|하절기|성수기|비수기|월별|매월|첫째|둘째|셋째|넷째|마지막\s*주|명절|설날|추석|공휴일|임시|변동|유동|예약|행사|일몰|일출|소진|점포|업소|층별|시설별|경우|별도|문의|날씨|기상|휴장|폐장|정비|연휴|일시|사정|예정|공사|체험별|프로그램|라스트|마지막\s*주문|last.?order|입장.*(?:마감|전)|매표.*(?:마감|전)|입장시간',re.I)
def read(path):
 with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def clean(x):return '' if x is None or str(x).strip() in ['', 'NULL','null','None','N/A'] else str(x).strip()
def closure_days(value):
 value=clean(value)
 if value=='연중무휴':return set()
 value=re.sub(r'^매주\s*','',value)
 if not value:raise ValueError('CLOSURE_POLICY_UNKNOWN')
 # Explicit weekly closures only. Public/seasonal and nth-week rules are held.
 days=set()
 for token in re.split(r'\s*[,、/]\s*',value):
  token=re.sub(r'^매주\s*','',token)
  m=re.fullmatch(r'([일월화수목금토])요일',token)
  if m:days.add(DOW[m[1]]);continue
  m=re.fullmatch(r'([일월화수목금토])요일?\s*[~～-]\s*([일월화수목금토])요일',token)
  if m:
   i=DOW[m[1]];end=DOW[m[2]]
   for _ in range(7):
    days.add(i)
    if i==end:break
    i=(i+1)%7
   continue
  raise ValueError('UNSUPPORTED_CLOSURE_RULE')
 return days
def clock(h,m):
 h,m=int(h),int(m)
 if not 0<=h<=24 or not 0<=m<60 or h==24 and m:raise ValueError('INVALID_CLOCK')
 return h*60+m
def normalize(hours,closure,effective_from):
 hours=clean(hours);closure=clean(closure)
 if not hours:raise ValueError('NO_HOURS_TEXT')
 if COMPLEX.search(hours+' '+closure) or re.search(r'\d\s*월|\d\s*일|\d/\d',hours+' '+closure):raise ValueError('CONDITIONAL_OR_CUTOFF_RULE')
 closed=closure_days(closure)
 # Day-qualified and 12-hour-clock expressions require a richer parser.
 if re.search(r'[일월화수목금토]\s*(?:요일|[~～-]\s*[일월화수목금토])|평일|주말|\b(?:am|pm)\b',hours,re.I):raise ValueError('DAY_SPECIFIC_OR_12H_RULE')
 found=list(RANGE.finditer(hours))
 if not found:raise ValueError('NO_EXPLICIT_TIME_RANGE')
 windows=[]
 for m in found:
  start,end=clock(m[1],m[2]),clock(m[3],m[4])
  if end==0:end=1440
  if end<=start:raise ValueError('OVERNIGHT_OR_EQUAL_TIME')
  windows.append([start,end])
 rest=RANGE.sub('',hours)
 rest=re.sub(r'매일|운영시간|영업시간|이용시간|관람시간|브레이크\s*타임|브레이크타임|break\s*time','',rest,flags=re.I)
 if re.sub(r'[\s/,:;|()\[\]·]+','',rest):raise ValueError('UNPARSED_HOURS_QUALIFIER')
 is_break=bool(re.search(r'브레이크|break',hours,re.I))
 if is_break:
  if len(windows)!=2:raise ValueError('UNSUPPORTED_BREAK_RULE')
  outer,br=windows
  if not outer[0]<br[0]<br[1]<outer[1]:raise ValueError('BREAK_OUTSIDE_OPENING')
  windows=[[outer[0],br[0]],[br[1],outer[1]]]
 windows.sort()
 result={'schema_version':'normalized-hours-1.0','effective_from':effective_from,'weekly':{str(d):[] if d in closed else windows for d in range(7)},'closed_policy_text':closure,'independently_verified':False,'basis':'matched official API; conservative automatic normalization'}
 return validate_schedule(result)
def build(evidence,inventory,metadata,oldops,effective_from):
 inv={r['place_id']:r for r in inventory};meta={r['place_id']:r for r in metadata};old={r['place_id']:r for r in oldops};grouped=collections.defaultdict(list)
 for e in evidence:
  if clean(e.get('hours_text')):grouped[e['place_id']].append(e)
 accepted=[];review=[]
 for pid,records in grouped.items():
  try:
   if pid not in meta or pid not in inv:raise ValueError('UNMATCHED_CATALOGUE_ID')
   if inv[pid]['source_time_range_disagreement']=='True':raise ValueError('SOURCE_RANGE_DISAGREEMENT')
   if inv[pid]['automatic_validation']=='REVIEW_IDENTITY' or meta[pid].get('detail_verification_status')=='CONFLICT':raise ValueError('IDENTITY_REVIEW_REQUIRED')
   if meta[pid].get('festival')=='1':raise ValueError('FESTIVAL_NOT_RECURRING')
   if clean(old.get(pid,{}).get('last_order_raw')):raise ValueError('EXISTING_LAST_ORDER_REQUIRES_REVIEW')
   parsed=[]
   for e in records:
    if e['automatic_validation']!='PASS_SOURCE_MATCH_AND_CONTENT':raise ValueError('SOURCE_VALIDATION_FAILED')
    datetime.fromisoformat(e['fetched_at_utc'])
    result=normalize(e['hours_text'],e['closure_text'],effective_from)
    if clean(old.get(pid,{}).get('break_times_raw')) and not re.search(r'브레이크|break',e['hours_text'],re.I) and not len(result['weekly'].get('1',[]))>1:raise ValueError('EXISTING_BREAK_REQUIRES_REVIEW')
    parsed.append(result)
   if len({json.dumps(p['weekly'],sort_keys=True) for p in parsed})!=1:raise ValueError('NORMALIZED_SOURCE_DISAGREEMENT')
   accepted.append({'place_id':pid,'place_name':meta[pid]['place_name'],'latitude':float(meta[pid]['latitude']),'longitude':float(meta[pid]['longitude']),'schedule':parsed[0],'evidence':[{k:e[k] for k in ['source','source_id','match_basis','source_endpoint','fetched_at_utc','hours_text','closure_text']} for e in records],'observed_at':min(e['fetched_at_utc'] for e in records)})
  except (ValueError,KeyError) as exc:review.append({'place_id':pid,'place_name':meta.get(pid,{}).get('place_name',''),'reason':str(exc),'hours_text':' || '.join(e['hours_text'] for e in records),'closure_text':' || '.join(e['closure_text'] for e in records)})
 return accepted,review
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--effective-from',default=date.today().isoformat());ap.add_argument('--out',type=Path,default=ROOT/'output/normalized_hours_20261119');a=ap.parse_args()
 date.fromisoformat(a.effective_from);a.out.mkdir(parents=True,exist_ok=True);data=ROOT/'src/ourney_travelsync/data';source=ROOT/'output/daily_poi_enrichment'
 files={'evidence':source/'evidence_latest.csv','inventory':source/'all_pois_latest.csv','metadata':data/'travelsync_poi_metadata_enriched_v3.csv','oldops':data/'travelsync_poi_operating_info_v1.csv'}
 accepted,review=build(**{k:read(p) for k,p in files.items()},effective_from=a.effective_from)
 manifest={'schema_version':'normalized-hours-batch-1.0','dataset_version':'busan-enriched-v3','effective_from':a.effective_from,'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files.values()},'records':sorted(accepted,key=lambda r:r['place_id'])}
 raw=json.dumps(manifest,ensure_ascii=False,sort_keys=True);manifest['batch_id']='normalized-hours-'+a.effective_from+'-'+hashlib.sha256(raw.encode()).hexdigest()[:12]
 (a.out/'normalized_hours.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 with (a.out/'held_for_review.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=['place_id','place_name','reason','hours_text','closure_text']);w.writeheader();w.writerows(review)
 summary={'accepted_pois':len(accepted),'held_pois':len(review),'weekly_windows':sum(len(w) for r in accepted for w in r['schedule']['weekly'].values()),'split_shift_pois':sum(any(len(w)>1 for w in r['schedule']['weekly'].values()) for r in accepted),'held_reasons':dict(collections.Counter(r['reason'] for r in review)),'batch_id':manifest['batch_id']}
 (a.out/'normalization_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
