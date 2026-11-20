import copy
import pytest
from sqlalchemy import create_engine, text
from scripts.promote_poi_enrichment import build_payload, apply_snapshot, fingerprint, get_state

def inputs():
    b={'place_id':'p','place_name':'Place','latitude':'35.1','longitude':'129.1','festival':'0'}
    e={'place_id':'p','place_name':'Place','source':'busan_attraction','source_id':'1',
       'source_endpoint':'https://apis.data.go.kr/6260000/AttractionService/getAttractionKr',
       'fetched_at_utc':'2026-11-21T00:00:00+00:00','hours_text':'10:00-18:00','closure_text':'연중무휴',
       'description_text':'Official description with source evidence','description_content_valid':'True',
       'automatic_validation':'PASS_SOURCE_MATCH_AND_CONTENT','match_basis':'EXACT_NAME_AND_DISTANCE'}
    i={'place_id':'p','place_name':'Place','hours_retrieved':'True','description_retrieved':'True',
       'source_time_range_disagreement':'False','automatic_validation':'PASS_SOURCE_MATCH_AND_CONTENT'}
    return {'p':{'description_summary':'Original description'}},[e],[i],[b],[],'2026-11-21'

def test_source_text_appends_without_losing_original_or_claiming_verification():
    args=inputs();result=build_payload(*args)
    assert args[0]['p']['description_summary']=='Original description'
    assert result['metadata']['p']['description_summary']=='Original description\n\nOfficial description with source evidence'
    assert result['metadata']['p']['description_enrichment_status']=='SOURCE_MATCHED_NOT_INDEPENDENTLY_VERIFIED'
    assert result['schedules'][0]['schedule']['weekly']['1']==[[600,1080]]

@pytest.mark.parametrize('kind',['time','identity','baseline_identity'])
def test_conflicts_stay_review_only(kind):
    args=inputs()
    if kind=='time':args[2][0]['source_time_range_disagreement']='True'
    elif kind=='identity':args[2][0]['automatic_validation']='REVIEW_IDENTITY'
    else:
        args[0]['p']['detail_verification_status']='CONFLICT'
        args[3][0]['detail_verification_status']='CONFLICT'
    r=build_payload(*args)
    assert r['metadata']['p']['description_summary']=='Original description'
    assert r['schedules']==[] and r['suppress_legacy_hours_ids']==['p']
    assert not r['evidence'][0]['description_usable']

def test_ambiguous_hours_do_not_block_independently_usable_description():
    args=inputs();args[1][0]['closure_text']=''
    r=build_payload(*args)
    assert not r['schedules'] and r['stats']['description_changed_pois']==1
    assert r['held_hours'][0]['reason']=='CLOSURE_POLICY_UNKNOWN'

def fixture():
    engine=create_engine('sqlite://')
    with engine.begin() as c:
        c.execute(text('CREATE TABLE dataset_versions(version TEXT PRIMARY KEY,status TEXT,feature_schema_version TEXT,mapping_version TEXT,poi_count INTEGER,description TEXT,created_at DATETIME,activated_at DATETIME)'))
        c.execute(text("INSERT INTO dataset_versions VALUES('old','ACTIVE','fs-1.0','rule_v2',3570,'base',NULL,NULL)"))
        c.execute(text('CREATE TABLE pois(id TEXT,dataset_version TEXT,name TEXT,lat FLOAT,lng FLOAT,field_status JSON,features JSON,PRIMARY KEY(id,dataset_version))'))
        c.execute(text('CREATE TABLE poi_metadata(place_id TEXT,dataset_version TEXT,metadata JSON,PRIMARY KEY(place_id,dataset_version))'))
        c.execute(text("INSERT INTO pois VALUES(:id,'old','Place',35.1,129.1,:status,:features)"),[{'id':str(i),'status':'{"hours":"KNOWN"}','features':'{"nature":1}'} for i in range(3570)])
        c.execute(text("INSERT INTO poi_metadata VALUES(:id,'old','{\"description_summary\":\"old\"}')"),[{'id':str(i)} for i in range(3570)])
        h=fingerprint(c,'old');_,meta=get_state(c,'old')
    return engine,{'parent_version':'old','dataset_version':'new','parent_database_sha256':h,'payload':{
        'metadata':meta,'schedules':[],'evidence':[],'review':[],'suppress_legacy_hours_ids':['0'],'stats':{}}}

def test_atomic_snapshot_preserves_history_and_core_values():
    engine,m=fixture();m['payload']['metadata']['1']['description_summary']='enriched'
    apply_snapshot(engine,m)
    with engine.connect() as c:
        assert fingerprint(c,'old')==m['parent_database_sha256']
        assert c.execute(text("SELECT version FROM dataset_versions WHERE status='ACTIVE'")).scalar_one()=='new'
        rows,meta=get_state(c,'new')
        assert len(rows)==3570 and meta['1']['description_summary']=='enriched'
        assert next(r for r in rows if r['id']=='0')['field_status']['hours']=='UNKNOWN'
        assert next(r for r in rows if r['id']=='1')['field_status']['hours']=='KNOWN'
    with pytest.raises(ValueError,match='already exists'):apply_snapshot(engine,m)

def test_stale_parent_rejects_without_creating_any_snapshot():
    engine,m=fixture();m['parent_database_sha256']='wrong'
    with pytest.raises(ValueError,match='changed'):apply_snapshot(engine,m)
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM dataset_versions')).scalar_one()==1

def test_failure_during_import_rolls_back_activation_and_all_inserts():
    engine,m=fixture();m['payload']['evidence']=[{'place_id':'0'}]
    with pytest.raises(KeyError):apply_snapshot(engine,m)
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM dataset_versions')).scalar_one()==1
        assert c.execute(text('SELECT count(*) FROM pois')).scalar_one()==3570
