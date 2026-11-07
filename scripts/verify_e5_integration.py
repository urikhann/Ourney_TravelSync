"""Local, label-free November 5 equivalence and real HTTP-service benchmark.

Requires the preserved experiment cache and commit 29018ce. Does not alter it.
Run from the repository root using the application environment.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

from ourney_travelsync import load_places, FEATURE_NAMES, GroupInput, GFAR
from ourney_travelsync.recommenders import hybrid as h
from ourney_travelsync.recommenders.pipeline import generate_candidates, prepare_group
from ourney_travelsync.recommenders.ties import ranking

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='output/e5-validation.json')
    args = parser.parse_args()
    source = subprocess.check_output(['git','show','29018ce:experiments/individual_scoring_20261105/scoring.py'], cwd=ROOT)
    reference = {'__file__': str(ROOT/'experiments/individual_scoring_20261105/scoring.py')}
    exec(compile(source, 'november5_scoring_reference', 'exec'), reference)
    config = json.loads(subprocess.check_output(['git','show','29018ce:experiments/individual_scoring_20261105/config.json'], cwd=ROOT))
    model = config['models'][1]
    key = hashlib.sha256(json.dumps([model, 'query: ', list(reference['LABELS'])], ensure_ascii=False).encode()).hexdigest()
    base = ROOT/'experiments/individual_scoring_20261105/cache'
    interests = np.load(base/(key+'.npy'), allow_pickle=False)
    with np.load(base/'multilingual-e5-small_description_scores.npz', allow_pickle=False) as f:
        ids, users, old_embeddings = tuple(f['ids'].tolist()), f['users'], f['embeddings']
    P = load_places()
    reorder = [P.place_ids.index(pid) for pid in ids]
    from dataclasses import replace
    P = replace(P, records=tuple(P.records[i] for i in reorder), place_ids=ids,
                features=P.features[reorder], coordinates=P.coordinates[reorder])
    old_feature, _ = reference['structured'](users, P.features)
    old_semantic = reference['semantic'](users, old_embeddings, interests, 1, 'weighted')
    expected = reference['hybrid'](old_feature, old_semantic, .5)
    fresh = h.score_catalogue(P, users)
    # Hold embeddings fixed to isolate scoring-math equivalence from CPU
    # inference roundoff. Never substitute or alter human pilot predictions.
    original_embeddings = h.embeddings
    h.embeddings = lambda places, cfg: (old_embeddings, interests, {'reference_commit':'29018ce'})
    result = h.score_catalogue(P, users)
    assert all(s['effective_scoring_method']=='e5_hybrid' for s in result.diagnostics['members'][1:]), result.diagnostics
    new_embeddings, new_interests, metadata = original_embeddings(P, h.ScoringConfig.from_env())
    np.testing.assert_allclose(new_embeddings, old_embeddings, atol=1e-7, rtol=1e-7)
    np.testing.assert_allclose(new_interests, interests, atol=1e-7, rtol=1e-7)
    np.testing.assert_allclose(result.scores, expected, atol=1e-12, rtol=1e-12)
    np.testing.assert_allclose(result.semantic_raw[1:], old_semantic[1:], atol=1e-12, rtol=1e-12)
    ranks_equal = all(np.array_equal(ranking(a, ids), ranking(b, ids)) for a,b in zip(result.scores, expected))
    assert ranks_equal
    prefs = {f'synthetic-{u}': dict(zip(FEATURE_NAMES, row)) for u,row in enumerate(users)}
    group = prepare_group(P, prefs)
    original = generate_candidates(GroupInput(tuple(prefs), ids, users, P.features, expected))
    assert group.poi_ids == original.poi_ids
    np.testing.assert_allclose(group.individual_scores, original.individual_scores, atol=1e-12)
    assert GFAR().recommend(group) == GFAR().recommend(original)
    h.embeddings = original_embeddings

    # Actual service contract on the real valid catalogue, not a separate demo API.
    spec = importlib.util.spec_from_file_location('e5_service_validation', ROOT/'services/recommender/main.py')
    service = importlib.util.module_from_spec(spec); spec.loader.exec_module(service)
    from fastapi.testclient import TestClient
    client = TestClient(service.app)
    forbidden = P.place_ids[0]
    request = dict(dataset_version=service.DATASET_VERSION, trip_id='controlled-e5-validation',
                   members=[dict(user_id=uid, ratings={k:int(v) for k,v in r.items()}, exclusions={'resolved':{forbidden:'raw_fish'}})
                            for uid,r in list(prefs.items())[4:8]], options={'tie_seed':0})
    response = client.post('/recommend', json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert all(s['effective_scoring_method']=='e5_hybrid' for s in body['scoring']['members'].values())
    assert body['scoring']['model_revision'] == h.MODEL_REVISION
    assert body['group'] and forbidden not in [g['poi_id'] for g in body['group']]
    assert any(r['poi_id']==forbidden and r['stage']=='E1' for r in body['removed'])
    assert all(1 <= g['group_utility'] <= 1000 for g in body['group'])
    assert all(g['selection_gain'] is not None for g in body['group'])
    timings=[]
    for _ in range(10):
        started=time.perf_counter()
        r=client.post('/recommend',json=request)
        timings.append(time.perf_counter()-started)
        assert r.status_code==200
        assert [g['poi_id'] for g in r.json()['group']] == [g['poi_id'] for g in body['group']]
    import psutil
    memory = psutil.Process().memory_info()
    # Deliberate semantic outage, preserving identical input and E1 constraints.
    original_embeddings = h.embeddings
    def unavailable(*a): raise h.SemanticUnavailable('semantic_model_unavailable')
    h.embeddings = unavailable
    try:
        fallback = client.post('/recommend',json=request).json()
        assert fallback['group'] and forbidden not in [g['poi_id'] for g in fallback['group']]
        assert all(s['fallback_reason']=='semantic_model_unavailable' for s in fallback['scoring']['members'].values())
    finally:
        h.embeddings = original_embeddings
    dataset = ROOT/'src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v3.csv'
    frozen_dataset = subprocess.check_output(['git','show','a80f48a:src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v3.csv'], cwd=ROOT)
    out = dict(reference_commit='29018ce', dataset_commit='a80f48a',
               dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
               dataset_matches_frozen_csv_ignoring_line_endings=dataset.read_bytes().replace(b'\r\n',b'\n')==frozen_dataset.replace(b'\r\n',b'\n'),
               catalogue_size=len(ids), synthetic_profiles=len(users),
               description_backed=sum(result.description_backed), fallback_text=len(ids)-sum(result.description_backed),
               model_id=h.MODEL_ID, model_revision=h.MODEL_REVISION,
               max_embedding_difference=float(np.abs(new_embeddings-old_embeddings).max()),
               max_interest_embedding_difference=float(np.abs(new_interests-interests).max()),
               max_semantic_difference=float(np.abs(result.semantic_raw[1:]-old_semantic[1:]).max()),
               max_hybrid_difference=float(np.abs(result.scores-expected).max()),
               fresh_encoding_max_hybrid_difference=float(np.abs(fresh.scores-expected).max()),
               fresh_encoding_identical_rankings=sum(np.array_equal(ranking(a,ids),ranking(b,ids)) for a,b in zip(fresh.scores,expected)),
               fresh_encoding_top10_membership_equal=sum(set(ranking(a,ids)[:10])==set(ranking(b,ids)[:10]) for a,b in zip(fresh.scores,expected)),
               all_individual_rankings_equal=ranks_equal, gfar_candidate_order_equal=True, gfar_top10_equal=True,
               real_service_hybrid_verified=True, hard_exclusions_verified=True, deliberate_outage_fallback_verified=True,
               warm_http_samples=10, group_members=4, warm_http_median_ms=1000*float(np.median(timings)),
               warm_http_p95_ms=1000*float(np.percentile(timings,95)),
               process_rss_mib=memory.rss/2**20, process_peak_working_set_mib=getattr(memory,'peak_wset',memory.rss)/2**20,
               performance_scope='CPU cached embeddings; in-process FastAPI HTTP, scoring, GFAR, JSON; excludes API DB/auth/network and cold encoding',
               november7_prediction_equivalence='NOT VERIFIED: original participant workbooks and generated predictions unavailable',
               cache_identity={k:v for k,v in metadata.items() if k!='poi_ids'})
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
