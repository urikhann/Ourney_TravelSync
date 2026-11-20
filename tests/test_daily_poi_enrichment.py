import json
import sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import collect_poi_enrichment_daily as daily

def init(tmp_path,monkeypatch,entries=(),clock=None):
 root=tmp_path/'repo';root.mkdir();monkeypatch.setattr(daily,'ROOT',root)
 out=tmp_path/'out';out.mkdir();(out/'requests.json').write_text(json.dumps(list(entries)),encoding='utf-8')
 return daily.Client(out,{},clock=clock or (lambda:datetime(2026,11,18,0,tzinfo=timezone.utc))),out

def test_quota_resets_at_korean_midnight(tmp_path,monkeypatch):
 endpoint=daily.TOUR+'searchKeyword2'
 old=[{'endpoint':endpoint,'params':{},'status':'RESERVED','fetched_at_utc':'2026-11-17T14:59:59+00:00'} for _ in range(1000)]
 c,_=init(tmp_path,monkeypatch,old,lambda:datetime(2026,11,17,15,10,tzinfo=timezone.utc))
 assert c.counts[endpoint]==0
 assert daily.request_day({'fetched_at_utc':'2026-11-17T15:00:00+00:00'}).isoformat()=='2026-11-18'

def test_exhausted_operation_does_not_send_or_spend_other_quota(tmp_path,monkeypatch):
 endpoint=daily.TOUR+'searchKeyword2'
 c,_=init(tmp_path,monkeypatch,[{'endpoint':endpoint,'params':{},'status':'RESERVED','fetched_at_utc':'2026-11-18T00:00:00+00:00'}]*1000)
 requests = pytest.importorskip('requests')
 monkeypatch.setattr(requests,'get',lambda *a,**k:pytest.fail('Quota guard must prevent network request'))
 assert c.get(endpoint,{'serviceKey':'private'},'keyword')[1]['status']=='DAILY_QUOTA_GUARD'
 assert c.counts[daily.TOUR+'detailIntro2']==0

def test_request_is_reserved_before_network_and_credentials_redacted(tmp_path,monkeypatch):
 c,out=init(tmp_path,monkeypatch);sentinel='SECRET_KEY_SENTINEL_1234';c.secrets={sentinel}
 requests = pytest.importorskip('requests')
 def get(*a,**k):
  logs=json.loads((out/'requests.json').read_text());assert logs[-1]['status']=='RESERVED'
  return SimpleNamespace(status_code=200,text=json.dumps({'response':{'header':{'resultCode':'0000'},'body':{'items':{'item':[{'overview':sentinel}]},'totalCount':1}}}))
 monkeypatch.setattr(requests,'get',get)
 payload,e=c.get(daily.TOUR+'detailCommon2',{'serviceKey':sentinel,'contentId':1},'common')
 assert e['status']=='OK' and payload['response']['body']['items']['item'][0]['overview']=='[REDACTED]'
 assert sentinel not in (out/'requests.json').read_text()
 assert all(sentinel not in p.read_text() for p in (out/'responses').glob('*'))

def test_xml_quota_stops_followup_requests(tmp_path,monkeypatch):
 c,_=init(tmp_path,monkeypatch)
 requests = pytest.importorskip('requests')
 calls=[]
 monkeypatch.setattr(requests,'get',lambda *a,**k:(calls.append(1) or SimpleNamespace(status_code=200,text='<error>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</error>')))
 endpoint=daily.TOUR+'detailIntro2'
 assert c.get(endpoint,{'contentId':1},'intro')[1]['status']=='PROVIDER_QUOTA_EXHAUSTED'
 assert c.get(endpoint,{'contentId':2},'intro')[1]['status']=='PROVIDER_QUOTA_EXHAUSTED'
 assert len(calls)==1

def test_cached_description_is_reused_without_quota(tmp_path,monkeypatch):
 c,out=init(tmp_path,monkeypatch);p=out/'responses/a.json'
 payload={'response':{'header':{'resultCode':'0000'},'body':{'items':{'item':[{'overview':'A useful description'}]}}}}
 p.write_text(json.dumps(payload));e={'endpoint':daily.TOUR+'detailCommon2','params':{'contentId':'1','MobileApp':'old'},'response_file':'responses/a.json','response_sha256':daily.sha(p),'fetched_at_utc':'2026-11-17T00:00:00+00:00','status':'OK'}
 c.cache[daily.signature(e['endpoint'],e['params'])]=(out,e)
 requests = pytest.importorskip('requests')
 monkeypatch.setattr(requests,'get',lambda *a,**k:pytest.fail('Cache should avoid requests'))
 assert c.get(e['endpoint'],{'contentId':1,'MobileApp':'new','serviceKey':'different'},'common',30)[0]==payload
 assert c.new_calls==0

def test_description_and_hours_validation_preserves_uncertainty():
 assert not daily.description_check('NULL','Venue')
 assert not daily.description_check('https://example.com/description','Venue')
 assert daily.description_check('해안 경관을 감상할 수 있는 문화시설이다.','Venue')
 assert daily.classify_schedule('상시 개방','연중무휴')['verified_for_planning'] is False
 assert daily.classify_schedule('동절기 09:00~17:00','매주 월요일')['schedule_evidence_status']=='VARIABLE_OR_CONDITIONAL_REQUIRES_REVIEW'

def test_matching_does_not_merge_branches():
 p={'place_id':'kakao:1','place_name':'카페 본점','latitude':'35.1','longitude':'129.1'}
 record={'UC_SEQ':1,'MAIN_TITLE':'카페 해운대점','LAT':35.1,'LNG':129.1}
 assert daily.match_record(p,[record],'busan')[0] is None

def test_output_cannot_overwrite_packaged_data(tmp_path,monkeypatch):
 monkeypatch.setattr(daily,'ROOT',tmp_path)
 args=SimpleNamespace(out=tmp_path/'src/ourney_travelsync/data',offline=True,max_new_calls=0)
 with pytest.raises(ValueError,match='packaged data'):daily.run(args)

def test_invalid_response_item_schema_is_rejected():
 with pytest.raises(ValueError):daily.items({'response':{'body':{'items':{'item':'wrong'}}}})

def test_atomic_checkpoint_retries_temporary_windows_lock(tmp_path,monkeypatch):
 original=Path.replace;calls=[]
 def flaky(path,target):
  calls.append(1)
  if len(calls)<3:raise PermissionError('temporary sharing lock')
  return original(path,target)
 monkeypatch.setattr(Path,'replace',flaky);monkeypatch.setattr(daily.time,'sleep',lambda _:None)
 destination=tmp_path/'checkpoint.json';destination.write_text('{"old":true}')
 daily.atomic_json(destination,{'new':True})
 assert json.loads(destination.read_text())=={'new':True} and len(calls)==3

def test_os_lock_prevents_concurrent_collection(tmp_path):
 with daily.exclusive_run(tmp_path/'lock'):
  with pytest.raises(RuntimeError,match='active'):
   with daily.exclusive_run(tmp_path/'lock'):pass
