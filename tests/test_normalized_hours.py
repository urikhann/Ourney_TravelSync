import sys,json,copy
from pathlib import Path
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import pytest
pytest.importorskip('sqlalchemy')
from sqlalchemy import create_engine,text
from scripts.normalize_poi_hours import normalize,build
from scripts.import_normalized_hours import apply_batch
from ourney_travelsync.operating_schedules import load_normalized,day_windows,validate_schedule

def test_explicit_weekly_closure_and_midnight():
 s=normalize('10:00~00:00','매주 월요일','2026-11-19')
 assert s['weekly']['1']==[] and s['weekly']['2']==[[600,1440]]

def test_split_shifts_and_breaks_remain_separate():
 assert normalize('11:00-15:00 / 17:00-22:00','연중무휴','2026-11-19')['weekly']['1']==[[660,900],[1020,1320]]
 assert normalize('11:00~22:00 (브레이크타임 15:00~17:00)','연중무휴','2026-11-19')['weekly']['1']==[[660,900],[1020,1320]]

@pytest.mark.parametrize('h,c',[('상시 개방','연중무휴'),('10:00-22:00',''),('동절기 10:00-18:00','연중무휴'),('10:00-22:00 라스트오더 21:00','연중무휴'),('18:00-02:00','연중무휴'),('25:00-26:00','연중무휴'),('10:00-20:00','매월 첫째 월요일'),('10:00-20:00','설날 당일'),('11:00-17:00 / 16:00-20:00','연중무휴')])
def test_unsupported_or_invalid_rules_are_held(h,c):
 with pytest.raises(ValueError):normalize(h,c,'2026-11-19')

def engine_fixture():
 e=create_engine('sqlite://')
 with e.begin() as c:
  c.execute(text('CREATE TABLE dataset_versions(version TEXT PRIMARY KEY,status TEXT)'))
  c.execute(text("INSERT INTO dataset_versions VALUES ('v3','ACTIVE'),('v1','ARCHIVED')"))
  c.execute(text('CREATE TABLE pois(id TEXT,dataset_version TEXT,name TEXT,lat REAL,lng REAL,PRIMARY KEY(id,dataset_version))'))
  c.execute(text("INSERT INTO pois VALUES ('p','v3','Place',35.1,129.1),('p','v1','Place',35.1,129.1)"))
 return e
def manifest():
 return {'schema_version':'normalized-hours-batch-1.0','batch_id':'batch-1','dataset_version':'v3','records':[{'place_id':'p','place_name':'Place','latitude':35.1,'longitude':129.1,'schedule':normalize('10:00-20:00','연중무휴','2026-11-19'),'evidence':[{'source_endpoint':'https://official.example','fetched_at_utc':'2026-11-17T00:00:00+00:00'}],'observed_at':'2026-11-17T00:00:00+00:00'}]}

def test_import_is_scoped_idempotent_and_effective_dated():
 e=engine_fixture();m=manifest()
 assert apply_batch(e,m,1)['imported_pois']==1
 assert apply_batch(e,m,1)['idempotent']
 with e.connect() as c:
  assert load_normalized(c,'v1',['p'])=={}
  r=load_normalized(c,'v3',['p'])['p']
  assert day_windows(r,date(2026,11,18)) is None
  assert day_windows(r,date(2026,11,19))==[[600,1200]]

def test_wrong_target_and_different_existing_batch_are_rejected():
 e=engine_fixture();m=manifest();bad=copy.deepcopy(m);bad['dataset_version']='v1'
 with pytest.raises(ValueError,match='active'):apply_batch(e,bad,1)
 apply_batch(e,m,1);bad=copy.deepcopy(m);bad['records'][0]['schedule']['weekly']['0']=[[600,1000]]
 with pytest.raises(ValueError,match='different content'):apply_batch(e,bad,1)

def test_invalid_batch_is_atomic():
 e=engine_fixture();m=manifest();m['records'].append({**m['records'][0],'place_id':'missing'})
 with pytest.raises(ValueError,match='identity'):apply_batch(e,m,1)
 with e.connect() as c:assert load_normalized(c,'v3',['p'])=={}

def test_normalized_windows_reach_the_real_planning_adapter(monkeypatch):
 import importlib.util
 root=Path(__file__).resolve().parents[1]
 monkeypatch.setenv('DATABASE_URL','sqlite://')
 spec=importlib.util.spec_from_file_location('normalized_adapter_test',root/'services/planning_data/main.py')
 adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
 e=engine_fixture()
 with e.begin() as c:
  for col in ['avg_cost_krw INTEGER','duration_min INTEGER','field_status TEXT','closed_on_public_holidays BOOLEAN','updated_at TEXT']:
   c.execute(text('ALTER TABLE pois ADD COLUMN '+col))
  c.execute(text("UPDATE pois SET duration_min=60,field_status='{}',closed_on_public_holidays=0"))
  c.execute(text('CREATE TABLE poi_hours(poi_id TEXT,dow INTEGER,open TEXT,close TEXT)'))
  c.execute(text('CREATE TABLE poi_exceptions(poi_id TEXT,day DATE,closed BOOLEAN,open TEXT,close TEXT,reason TEXT)'))
  c.execute(text('CREATE TABLE public_holidays(day DATE,name TEXT)'))
 apply_batch(e,manifest(),1);monkeypatch.setattr(adapter,'engine',e)
 days=[date(2026,11,18),date(2026,11,19)]
 facts=adapter._load_operating('v3',['p'],days)['p']['operations']
 assert facts['2026-11-18']['openings']['status']=='UNKNOWN'
 assert facts['2026-11-19']['openings']['value']==[{'start_min':600,'end_min':1200}]
 assert facts['2026-11-19']['closed']['value'] is False
 assert adapter._load_operating('v1',['p'],[days[1]])['p']['operations']['2026-11-19']['openings']['status']=='UNKNOWN'

def test_availability_respects_split_shifts_and_dataset_version(monkeypatch):
 import importlib.util
 root=Path(__file__).resolve().parents[1]
 sys.path.insert(0,str(root/'services/mocks'));monkeypatch.setenv('DATABASE_URL','sqlite://')
 spec=importlib.util.spec_from_file_location('normalized_availability_test',root/'services/mocks/main.py')
 checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
 e=engine_fixture()
 with e.begin() as c:
  c.execute(text('ALTER TABLE pois ADD COLUMN closed_on_public_holidays BOOLEAN'))
  c.execute(text('UPDATE pois SET closed_on_public_holidays=0'))
  c.execute(text('CREATE TABLE poi_hours(poi_id TEXT,dow INTEGER,open TEXT,close TEXT)'))
  c.execute(text('CREATE TABLE poi_exceptions(poi_id TEXT,day DATE,closed BOOLEAN,open TEXT,close TEXT,reason TEXT)'))
  c.execute(text('CREATE TABLE public_holidays(day DATE,name TEXT)'))
 m=manifest();m['records'][0]['schedule']=normalize('11:00-15:00 / 17:00-22:00','연중무휴','2026-11-19')
 apply_batch(e,m,1);monkeypatch.setattr(checker,'engine',e)
 assert checker.check_one('p',date(2026,11,19),'16:00',30,'v3')['available'] is False
 assert checker.check_one('p',date(2026,11,19),'14:30',30,'v3')['available'] is False
 assert checker.check_one('p',date(2026,11,19),'17:00',60,'v3')['available'] is True
 assert checker.check_one('p',date(2026,11,19),'17:00',60,'v1')['available'] is None
