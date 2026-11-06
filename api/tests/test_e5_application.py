"""Real API -> versioned SQLite JOIN -> production HTTP recommender -> run JSON.

Pinned mock embeddings keep routine tests offline. The separate verification
script exercises the actual E5-generated catalogue embeddings.
"""
from datetime import date
import os
import json
import time

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db import Base, get_db
from app.models import User, Trip, TripMember, Preference, RecommendationRun, Poi
from app.import_places import import_csv
from app.routes.auth import current_user
from ourney_travelsync import FEATURE_NAMES
from ourney_travelsync.recommenders import hybrid as h
from pathlib import Path


def test_real_dataset_application_hybrid_and_fallback(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        path=Path(__file__).resolve().parents[2]/'src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v3.csv'
        assert import_csv(db,path,'busan-enriched-v3')==3570
        users=[User(id='controlled-a',email='a@example.test',name='A',password_hash='unused'),
               User(id='controlled-b',email='b@example.test',name='B',password_hash='unused')]
        db.add_all(users)
        trip=Trip(id='controlled-e5',name='E5',city='busan',created_by=users[0].id,start_date=date(2026,11,7),end_date=date(2026,11,7))
        db.add(trip); db.flush()
        forbidden=db.scalar(select(Poi.id).where(Poi.dataset_version=='busan-enriched-v3'))
        for i,u in enumerate(users):
            db.add(TripMember(trip_id=trip.id,user_id=u.id,role='owner' if i==0 else 'member',
                              exclusions={'poi_ids':[forbidden], 'tags':['raw_fish']}))
            ratings=[5,1,4,2,3,3,3,3,3,3] if i==0 else [1,5,2,4,3,3,3,3,3,3]
            db.add(Preference(trip_id=trip.id,user_id=u.id,version=1,ratings=dict(zip(FEATURE_NAMES,ratings))))
        db.commit()
        def embed(P,config):
            # Exercise complete metadata mapping and full catalogue input.
            assert len(P.place_ids)==3570 and all(r['place_name'] and r['category_name'] for r in P.records)
            assert sum(h.document(r)[1] for r in P.records)==2249
            rng=np.random.default_rng(42)
            a=rng.normal(size=(3580,384)).astype('float32'); a/=np.linalg.norm(a,axis=1,keepdims=True)
            return a[:-10],a[-10:],{'dataset_content_hash':'controlled-fixture'}
        if os.getenv('TRAVELSYNC_REAL_E5_INTEGRATION') != '1':
            monkeypatch.setattr(h,'embeddings',embed)
        def session(): yield db
        app.dependency_overrides[get_db]=session
        app.dependency_overrides[current_user]=lambda:users[0]
        try:
            client=TestClient(app)
            response=client.post(f'/trips/{trip.id}/recommendations',json={})
            assert response.status_code==200,response.text
            body=response.json()
            assert body['individual_method']=='e5_hybrid' and body['scoring']['model_revision']==h.MODEL_REVISION
            assert all(m['effective_scoring_method']=='e5_hybrid' for m in body['scoring']['members'].values())
            assert body['group'] and forbidden not in {g['poi_id'] for g in body['group']}
            assert all(g['fit'] is None for g in body['group'])
            run=db.scalar(select(RecommendationRun).where(RecommendationRun.id==body['run_id']))
            assert run.request['options']['individual_method']=='e5_hybrid'
            assert len(run.request['poi_snapshot'])==3570
            assert run.response['individual'][users[0].id][0]['scoring_method']=='e5_hybrid'
            raw_fish={p.id for p in db.scalars(select(Poi)) if 'raw_fish' in p.tags}
            assert raw_fish and not raw_fish & {g['poi_id'] for g in body['group']}
            if os.getenv('TRAVELSYNC_REAL_E5_INTEGRATION') == '1':
                import psutil
                timings=[]
                for _ in range(5):
                    started=time.perf_counter()
                    warm=client.post(f'/trips/{trip.id}/recommendations',json={})
                    timings.append(1000*(time.perf_counter()-started))
                    assert warm.status_code==200
                    assert all(m['effective_scoring_method']=='e5_hybrid' for m in warm.json()['scoring']['members'].values())
                memory=psutil.Process().memory_info()
                out=dict(samples=5, members=2, catalogue_size=3570, warm_median_ms=float(np.median(timings)),
                         warm_p95_ms=float(np.percentile(timings,95)), process_peak_working_set_mib=getattr(memory,'peak_wset',memory.rss)/2**20,
                         scope='API route, SQLite versioned JOIN, in-process HTTP recommender, actual E5 cache, GFAR, request/response persistence; auth dependency overridden, no network or cold encoding')
                output=Path(__file__).resolve().parents[2]/'output/e5-api-performance.json'
                output.parent.mkdir(parents=True,exist_ok=True)
                output.write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
            def fail(*a): raise h.SemanticUnavailable('semantic_model_unavailable')
            monkeypatch.setattr(h,'embeddings',fail)
            response=client.post(f'/trips/{trip.id}/recommendations',json={})
            assert response.status_code==200
            fallback=response.json()
            assert fallback['group'] and forbidden not in {g['poi_id'] for g in fallback['group']}
            assert all(m['fallback_reason']=='semantic_model_unavailable' for m in fallback['scoring']['members'].values())
            assert client.post(f'/trips/{trip.id}/recommendations',json={'alpha':-1}).status_code==422
            assert client.post(f'/trips/{trip.id}/recommendations',json={'method':'awm','awm_threshold':.25}).status_code==422
        finally:
            app.dependency_overrides.clear()
    engine.dispose()
