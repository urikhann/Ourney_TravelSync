from copy import deepcopy
from uuid import UUID
from contextlib import nullcontext
import numpy as np
import pytest
from ourney_travelsync import FEATURE_NAMES
from ourney_travelsync.db.adapter import adapt_run_inputs, compute_snapshot, recommend_database


def snapshot():
    uid = UUID(int=1); other = UUID(int=2); ds = UUID(int=10)
    pois = [dict(poi_id=UUID(int=100+i), place_id=f'external:{i}', dataset_version=ds,
                 latitude=35.1, longitude=129.1, category_name='cafe' if i else 'food',
                 **dict(zip(FEATURE_NAMES, np.eye(10, dtype=int)[i].tolist()))) for i in range(2)]
    prefs = [dict(user_id=u, version=ver, **(dict.fromkeys(FEATURE_NAMES, 3) | dict(food=food, cafe=cafe)))
             for u, ver, food, cafe in [(other, 7, 1, 5), (uid, 4, 5, 1)]]
    return dict(run=dict(dataset_version=ds, member_snapshot=[dict(user_id=str(u), preference_version=v)
                    for u,v in [(uid,4),(other,7)]], config_snapshot=dict(algorithm='gfar', tie_seed=0,
                    candidate_top_n=100, gfar_depth=20, misery_threshold=2.5, top_k=2)),
                pois=pois[::-1], preferences=prefs, constraints=dict(member_exclusions={}))


def test_alignment_and_separate_result_semantics():
    data = snapshot(); adapted = adapt_run_inputs(data)
    assert tuple(adapted.preferences) == (str(UUID(int=1)), str(UUID(int=2)))
    assert adapted.places.place_ids == (str(UUID(int=100)), str(UUID(int=101)))
    np.testing.assert_equal(adapted.places.features, np.eye(10)[:2])
    results, individual, group = compute_snapshot(data)
    assert {r.poi_id for r in results} == set(adapted.places.place_ids)
    assert sorted(r['score'] for r in individual) == [1,1,5,5]
    assert all(r['mean_score'] == 3 and r['selection_gain'] is not None for r in group)
    assert all(r['group_utility'] is None and r['feasible_dates'] is None for r in group)
    data['run']['config_snapshot'].update(algorithm='awm', misery_threshold=1)
    assert all(r['selection_gain'] is None for r in compute_snapshot(data)[2])


def test_exclusions_and_ineligible_rank():
    data = snapshot(); uid = str(UUID(int=1))
    data['constraints']['member_exclusions'] = {uid: dict(categories=['food'])}
    results, individual, _ = compute_snapshot(data)
    assert [r.poi_id for r in results] == [str(UUID(int=101))]
    assert all(r['rank'] is None and not r['eligible'] for r in individual if r['poi_id'] == str(UUID(int=100)))
    data['constraints']['member_exclusions'] = {uid: dict(poi_ids=[str(UUID(int=101))])}
    assert [r.poi_id for r in compute_snapshot(data)[0]] == [str(UUID(int=100))]


@pytest.mark.parametrize('fault', ['missing', 'version', 'feature', 'dataset', 'duplicate', 'preference'])
def test_invalid_snapshots(fault):
    data = snapshot()
    if fault == 'missing': data['preferences'].pop()
    if fault == 'version': data['preferences'][0]['version'] = 999
    if fault == 'feature': data['pois'][0]['food'] = .5
    if fault == 'dataset': data['pois'][0]['dataset_version'] = 'wrong'
    if fault == 'duplicate': data['pois'].append(deepcopy(data['pois'][0]))
    if fault == 'preference': del data['preferences'][0]['food']
    with pytest.raises((ValueError, TypeError)): adapt_run_inputs(data)


def test_persistence_uses_captured_inputs(monkeypatch):
    repository = pytest.importorskip('ourney_travelsync.db.repository')
    data = snapshot(); calls = []
    class Connection:
        def transaction(self): return nullcontext()
        def execute(self, statement, values): calls.append(statement)
    monkeypatch.setattr(repository, 'create_recommendation_run', lambda *args: 'captured-run')
    monkeypatch.setattr(repository, 'get_run_inputs', lambda conn, rid: data)
    monkeypatch.setattr(repository, 'get_latest_preference', lambda *args: pytest.fail('mutable preference read'))
    monkeypatch.setattr(repository, 'get_active_pois', lambda *args: pytest.fail('mutable POI read'))
    monkeypatch.setattr(repository, 'save_individual_scores', lambda conn, rid, rows: calls.append(('individual', len(rows))))
    monkeypatch.setattr(repository, 'save_group_scores', lambda conn, rid, rows: calls.append(('group', len(rows))))
    rid, results = recommend_database(Connection(), 'group', 'constraint', data['run']['config_snapshot'])
    assert rid == 'captured-run' and len(results) == 2
    assert ('individual',4) in calls and ('group',2) in calls
