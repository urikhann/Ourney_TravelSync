from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import main
from ourney_travelsync import FEATURE_NAMES
from ourney_travelsync.recommenders import hybrid as h

client = TestClient(main.app)
EXAMPLE = json.loads((Path(__file__).resolve().parents[3]/'contracts/recommend.request.json').read_text())


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    def embed(places, config):
        rng = np.random.default_rng(17)
        a = rng.normal(size=(len(places.place_ids)+10,384)).astype('float32')
        a /= np.linalg.norm(a,axis=1,keepdims=True)
        return a[:len(places.place_ids)], a[-10:], {'dataset_content_hash':'fixture'}
    monkeypatch.setattr(h,'embeddings',embed)


def request(**options):
    req = deepcopy(EXAMPLE)
    req['options'] = {'tie_seed':0, **options}
    return req


def test_default_hybrid_separate_members_and_components():
    r = client.post('/recommend',json=request())
    assert r.status_code==200, r.text
    b=r.json()
    assert b['individual_method']=='e5_hybrid' and b['score_scale']=='relative_relevance_0_1'
    assert b['scoring']['model_id']==h.MODEL_ID and b['scoring']['model_revision']==h.MODEL_REVISION
    assert all(m['effective_scoring_method']=='e5_hybrid' for m in b['scoring']['members'].values())
    assert all(s is None for s in b['metrics']['member_satisfaction'].values())
    lists = list(b['individual'].values())
    assert [r['poi_id'] for r in lists[0]] != [r['poi_id'] for r in lists[1]]
    row=lists[0][0]
    assert row['score']==round(.5*row['feature_score_normalized']+.5*row['semantic_score_normalized'],4)
    assert row['scoring_method']=='e5_hybrid'


def test_model_failure_and_neutral_member(monkeypatch):
    req=request()
    uid=req['members'][0]['user_id']
    req['members'][0]['ratings']=dict.fromkeys(FEATURE_NAMES,3)
    body=client.post('/recommend',json=req).json()
    assert body['scoring']['members'][uid]['fallback_reason']=='all_neutral_preferences'
    assert any(s['effective_scoring_method']=='e5_hybrid' for s in body['scoring']['members'].values())
    def fail(*a): raise RuntimeError('sensitive path')
    monkeypatch.setattr(h,'embeddings',fail)
    body=client.post('/recommend',json=req).json()
    assert body['group'] and 'sensitive' not in json.dumps(body)
    assert all(s['effective_scoring_method']=='feature_mean' for s in body['scoring']['members'].values())


def test_exhaustion_and_no_hard_relaxation():
    req=request()
    req['members'][0]['exclusions']={'poi_ids':list(main.places().place_ids)}
    body=client.post('/recommend',json=req).json()
    assert body['status']=='exhausted' and body['exhaustion_reason']=='hard_constraints'
    assert not body['group'] and len(body['removed'])==3570


def test_awm_routes_feature_and_rejects_incompatible_thresholds():
    b=client.post('/recommend',json=request(method='awm')).json()
    assert b['individual_method']=='feature_mean' and b['score_scale']=='1-5'
    assert b['requested_scoring_method']=='e5_hybrid'
    assert client.post('/recommend',json=request(method='awm',individual_method='tfidf')).status_code==422
    assert client.post('/recommend',json=request(method='awm',awm_threshold=.25)).status_code==422
    assert client.post('/recommend',json=request(alpha=-.1)).status_code==422


def test_single_member_snapshot_and_fallback_text():
    req=request()
    req['members']=req['members'][:1]
    req['poi_snapshot']=[dict(place_id='only',place_name='실제 이름',category_name='카페',dataset_version=req['dataset_version'],
                              latitude=35.1,longitude=129.1,**{f:int(f=='cafe') for f in FEATURE_NAMES})]
    body=client.post('/recommend',json=req).json()
    assert body['group'][0]['poi_id']=='only'
    row=next(iter(body['individual'].values()))[0]
    assert row['hybrid_score']==.5 and not row['description_backed']
