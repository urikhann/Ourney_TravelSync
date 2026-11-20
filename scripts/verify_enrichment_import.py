"""Readback and live-service checks; no users, runs or itineraries are written."""
import collections,csv,json,os,subprocess,sys,urllib.request
from pathlib import Path
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from sqlalchemy import text
from scripts.promote_poi_enrichment import local_engine,digest,fingerprint,get_state
from scripts.targeted_enrichment import category
from ourney_travelsync.recommenders.hybrid import document
from ourney_travelsync.recommenders.tfidf import document as tfidf_document
from ourney_travelsync.places import load_places

def http(path,payload=None,port=8002):
    request=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=180) as r:return json.load(r)

def main():
    out=ROOT/'output/enrichment_import_20261121';m=json.loads((out/'manifest.json').read_text(encoding='utf-8'));before=json.loads((out/'before_state.json').read_text(encoding='utf-8'));v=m['dataset_version'];payload=m['payload']
    with local_engine().connect() as c:
        assert c.execute(text("SELECT version FROM dataset_versions WHERE status='ACTIVE'")).scalar_one()==v
        assert fingerprint(c,m['parent_version'])==m['parent_database_sha256']
        rows,meta=get_state(c,v);assert meta==payload['metadata'] and len(rows)==3570
        for table,expected in before.items():
            if not isinstance(expected,dict) or 'sha256' not in expected:continue
            actual=[dict(r) for r in c.execute(text('SELECT * FROM '+table+' ORDER BY id')).mappings()]
            assert len(actual)==expected['count'] and digest(actual)==expected['sha256'],table+' changed'
        assert c.execute(text('SELECT count(*) FROM poi_enrichment_evidence WHERE dataset_version=:v'),{'v':v}).scalar_one()==len(payload['evidence'])
        assert c.execute(text('SELECT count(*) FROM poi_enrichment_reviews WHERE dataset_version=:v'),{'v':v}).scalar_one()==len(payload['review'])
        hours=[dict(r) for r in c.execute(text('SELECT poi_id,schedule FROM poi_normalized_hours WHERE dataset_version=:v'),{'v':v}).mappings()]
        assert {r['poi_id']:r['schedule'] for r in hours}=={r['place_id']:r['schedule'] for r in payload['schedules']}
    byid={r['id']:r for r in rows};baseline={r['place_id']:r for r in load_places().records}
    for pid in payload['description_changed_ids']:
        record={'place_name':byid[pid]['name'],'category_name':byid[pid]['category'],'enriched_metadata':meta[pid]}
        doc,backed=document(record)
        assert backed and meta[pid]['description_summary'] in doc
        assert meta[pid]['description_summary'] in tfidf_document(record)
    csvfile=ROOT/'src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v4.csv'
    exported=load_places(csvfile);assert len(exported.place_ids)==3570
    assert all(r['description_summary']==(meta[r['place_id']].get('description_summary') or 'NULL') for r in exported.records)
    health={name:http('/health',port=port) for name,port in [('api',8000),('recommender',8002),('optimizer',8003),('planning-data',8004)]}
    assert health['recommender']['dataset_version']==v
    # The actual adapter container reads every imported schedule over a full week.
    checks=[{'id':r['place_id'],'weekly':r['schedule']['weekly']} for r in payload['schedules']]
    script="""import json\nfrom datetime import date,timedelta\nimport main\nchecks=json.loads(DATA)\ndays=[date(2026,11,21)+timedelta(days=i) for i in range(7)]\ndata=main._load_operating(VERSION,[r['id'] for r in checks],days)\ncount=0\nfor r in checks:\n for d in days:\n  fact=data[r['id']]['operations'][d.isoformat()]['openings']\n  expected=[{'start_min':a,'end_min':b} for a,b in r['weekly'][str((d.weekday()+1)%7)]]\n  assert fact['value']==expected,(r['id'],str(d))\n  assert fact['source'].startswith('normalized-hours:')\n  count+=1\nprint(json.dumps({'poi_day_checks':count,'all_windows_match':True,'sample':data[checks[-1]['id']]}))\n""".replace('DATA',repr(json.dumps(checks))).replace('VERSION',repr(v))
    result=subprocess.run(['docker','exec','-i','ourney_travelsync-planning-data-1','python','-'],input=script,text=True,capture_output=True,check=True)
    operating=json.loads(result.stdout);sample=operating.pop('sample')
    # Direct live recommender request carries the same DB snapshot fields as the API.
    features=['food','cafe','nature','history','art','shopping','activity','entertainment','relax','festival']
    records=[{'place_id':r['id'],'dataset_version':v,'place_name':r['name'],'category_name':r['category'],
              'latitude':r['lat'],'longitude':r['lng'],'enriched_metadata':meta[r['id']],**r['features']} for r in rows]
    req={'schema_version':'rec-1.0','trip_id':'enrichment-readonly-smoke','city':'busan','dataset_version':v,
         'feature_schema_version':'fs-1.0','features':features,'dates':['2026-11-21'],
         'members':[{'user_id':'smoke-member','preference_version':1,'ratings':{f:5 if f in ['nature','history'] else 3 for f in features},'exclusions':{'poi_ids':[],'tags':[]}}],
         'options':{'method':'gfar','individual_method':'e5_hybrid','top_l':100,'group_k':5,'tie_seed':0},'poi_snapshot':records}
    rec=http('/recommend',req);assert rec['dataset_version']==v and rec['individual_method']=='e5_hybrid' and rec['group']
    assert all(r['effective_scoring_method']=='e5_hybrid' and r.get('fallback_reason') is None for r in rec['scoring']['members'].values()), 'enriched text scoring fell back'
    (out/'recommender_smoke_response.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2),encoding='utf-8')
    # Real optimizer, real imported opening facts; controlled co-located endpoints,
    # zero-distance test edges. This does not claim external routing verification.
    pid=checks[-1]['id'];day='2026-11-21';fact=sample['operations'][day]
    windows=fact['openings']['value'];assert windows
    window=windows[0];start,end=window['start_min'],window['end_min'];duration=min(30,end-start)
    candidate={'poi_id':pid,'dataset_version':v,'group_rank':1,'group_utility':1000,'mean_score':1.,'personal_scores':{'smoke-member':1.},'relevance':{'smoke-member':1.},
               'visit_category':'activity','activity_category':'other','duration_min':{'value':duration,'status':'ESTIMATED','source':'explicit smoke-test duration','verified_at':m['effective_from']+'T00:00:00Z','rule_version':'smoke-1'},'operations':{day:fact}}
    edges=[{'date':day,'origin':a,'destination':b,'status':'KNOWN','duration_minutes':0,'distance_m':0,'group_fare':0,'walk_minutes':0,'provider':'CONTROLLED_TEST_COLOCATED_POINTS','fetched_at':m['effective_from']+'T00:00:00Z'} for a in ['start',pid,'end'] for b in ['start',pid,'end'] if a!=b]
    solver={'schema_version':'optimizer-1.0','run_id':'enrichment-readonly-smoke','dataset_version':v,'constraint_version':'smoke-1','preference_version':'smoke-1','mapping_version':'rule_v2','feature_schema_version':'fs-1.0','operating_snapshot_id':'database-normalized-hours-readback','routing_version':'CONTROLLED_TEST_COLOCATED_POINTS','transport_mode':'drive',
            'days':[{'date':day,'start_min':start,'end_min':end,'start_point':'start','end_point':'end'}],
            'members':[{'member_id':'smoke-member','available':{day:[{'start_min':start,'end_min':end}]}}],
            'candidates':[candidate],'edges':edges,'budget_total':0,'enforce_cost':False,'enforce_opening_hours':True,'allow_unknown_opening_hours':False,'daily_composition':None,'search_seconds':2,'seed':0}
    plan=http('/plans',solver,8003)
    assert plan['solver_status'] in ['OPTIMAL','FEASIBLE'] and plan['validation_status']=='PASSED'
    assert plan['input_snapshot']['dataset_version']==v and plan['metrics']['visit_count']==1
    (out/'optimizer_smoke_request.json').write_text(json.dumps(solver,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'optimizer_smoke_response.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    categories={}
    for cat in ['attraction','restaurant','cafe','other']:
        ids={pid for pid,b in baseline.items() if (category(b) if category(b) in ['attraction','restaurant','cafe'] else 'other')==cat}
        categories[cat]={'pois':len(ids),'descriptions_enriched':len(ids & set(payload['description_changed_ids'])),
                         'new_descriptions':len(ids & set(payload['new_description_ids'])),
                         'normalized_hours':len(ids & {r['place_id'] for r in payload['schedules']}),
                         'held_hours':len(ids & {r['place_id'] for r in payload['held_hours']})}
    verification={'dataset_version':v,'parent_immutable':True,'legacy_and_historical_tables_unchanged':True,'metadata_readback_matches':True,
                  'raw_evidence_rows':len(payload['evidence']),'review_rows':len(payload['review']),
                  'recommender_documents_checked':len(payload['description_changed_ids']),'recommender_returned':len(rec['group']),
                  'recommender_scoring':rec.get('scoring'),'recommender_score_semantics':rec.get('score_semantics'),
                  'planner_readback':operating,'optimizer_status':plan['solver_status'],'optimizer_validation':plan['validation_status'],'optimizer_result':plan,
                  'routing_smoke_uses_controlled_test_edges':True,'categories':categories,'service_health':health}
    (out/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in verification.items() if k not in ['optimizer_result','service_health','recommender_scoring']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
