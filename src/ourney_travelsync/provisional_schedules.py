"""Source-preserving provisional schedules. Unsupported clauses stay unresolved."""
import re
from datetime import date
RANGE=re.compile(r'(?<!\d)(\d{1,2}):(\d{2})\s*[~～–—-]\s*(\d{1,2}):(\d{2})(?!\d)')
DAYS='일월화수목금토'
def minutes(h,m):
 h,m=int(h),int(m)
 if not 0<=h<=24 or not 0<=m<60 or h==24 and m:raise ValueError('INVALID_CLOCK')
 return h*60+m

def provisional(evidence,metadata,effective_from):
 rules=[];resolved=[];issues=[]
 for e in evidence:
  hours=e.get('hours_text','').strip();closure=e.get('closure_text','').strip()
  if not hours:continue
  ranges=[]
  for match in RANGE.finditer(hours):
   try:ranges.append([minutes(match[1],match[2]),minutes(match[3],match[4])])
   except ValueError:issues.append('INVALID_CLOCK')
  rules.append({'hours_text':hours,'closure_text':closure,'reported_ranges':ranges,'source':e['source'],'source_id':e['source_id'],'fetched_at_utc':e['fetched_at_utc']})
  # Only fully understood ordinary intervals are expanded. Text for seasons,
  # subfacilities, lunar holidays, reservations and changing rules stays intact.
  text=hours;closed=set();local=[]
  cut=re.search(r'(?:마지막\s*주문|라스트\s*오더|입장\s*마감|발권\s*마감|매표\s*마감)\s*(\d{1,2}):(\d{2})',text)
  cutoff=minutes(cut[1],cut[2]) if cut else None
  if cut:text=text[:cut.start()]+text[cut.end():]
  matches=list(RANGE.finditer(text));windows=[]
  for match in matches:
   a,b=minutes(match[1],match[2]),minutes(match[3],match[4]);b=1440 if b==0 else b
   if a>=b:local.append('OVERNIGHT_OR_INVALID_RANGE')
   windows.append([a,b])
  rest=RANGE.sub('',text)
  is_break=bool(re.search(r'브레이크\s*타임|휴게시간|준비시간|break\s*time',rest,re.I))
  rest=re.sub(r'매일|운영시간|영업시간|이용시간|관람시간|브레이크\s*타임|휴게시간|준비시간|break\s*time','',rest,flags=re.I)
  if re.sub(r'[\s/,:;|()\[\]·]+','',rest):local.append('UNRESOLVED_SCOPE_OR_CONDITIONS')
  if is_break:
   if len(windows)==2 and windows[0][0]<windows[1][0]<windows[1][1]<windows[0][1]:
    outer,br=windows;windows=[[outer[0],br[0]],[br[1],outer[1]]]
   else:local.append('UNRESOLVED_BREAK')
  if metadata.get('break_times_raw') and not is_break:local.append('HISTORICAL_BREAK_NOT_CONFIRMED')
  if metadata.get('last_order_raw') and cutoff is None:local.append('HISTORICAL_CUTOFF_NOT_CONFIRMED')
  unknown_closure=not closure
  if closure and closure!='연중무휴':
   c=re.sub(r'매주|요일|\s','',closure)
   if re.fullmatch(r'[일월화수목금토](?:[,/、][일월화수목금토])*',c):closed={DAYS.index(d) for d in re.findall('[일월화수목금토]',c)}
   else:local.append('UNRESOLVED_CLOSURE_RULE')
  if not windows:local.append('NO_ORDINARY_WINDOW')
  if cutoff is not None:
   windows=[[a,min(b,cutoff)] for a,b in windows if a<min(b,cutoff)]
   if not windows:local.append('CUTOFF_OUTSIDE_WINDOW')
  windows.sort()
  if any(a[1]>b[0] for a,b in zip(windows,windows[1:])):local.append('OVERLAPPING_RANGES')
  if local:issues.extend(local)
  else:resolved.append({str(d):[] if d in closed else windows for d in range(7)})
 if not rules:raise ValueError('provisional schedule has no source hours')
 if len(resolved)!=len(rules) or len({str(w) for w in resolved})!=1:
  weekly={str(d):None for d in range(7)}
  if len(resolved)>1:issues.append('SOURCE_RULE_EXPANSION_DIFFERS')
 else:weekly=resolved[0]
 return validate_provisional({'schema_version':'provisional-hours-1.0','effective_from':effective_from,
  'weekly':weekly,'source_rules':rules,'unresolved_rules':sorted(set(issues)),
  'review_required':True,'independently_verified':False,'planning_status':'ESTIMATED',
  'basis':'User-authorized provisional import; explicit ranges only; closure/condition review retained',
  'assumptions':['Ordinary intervals are provisional, not confirmation that the venue opens on a requested date.',
                 'When an explicit last-order/admission cutoff is parsed, the whole visit must finish before it.']})

def validate_provisional(s):
 if s.get('schema_version')!='provisional-hours-1.0':raise ValueError('invalid provisional schema')
 date.fromisoformat(s['effective_from'])
 if s.get('independently_verified') is not False or s.get('review_required') is not True or s.get('planning_status')!='ESTIMATED':raise ValueError('provisional uncertainty flags missing')
 if not s.get('source_rules') or set(s['weekly'])!=set(map(str,range(7))):raise ValueError('incomplete provisional rules')
 for windows in s['weekly'].values():
  if windows is None:continue
  end=-1
  for a,b in windows:
   if type(a) is not int or type(b) is not int or not 0<=a<b<=1440 or a<end:raise ValueError('invalid provisional window')
   end=b
 return s
