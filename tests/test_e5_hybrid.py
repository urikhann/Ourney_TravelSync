"""Network-free scoring, cache and pipeline regressions using pinned embeddings."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import types

import numpy as np
import pytest

from ourney_travelsync import FEATURE_NAMES, GFAR, AverageWithoutMisery
from ourney_travelsync.places import PlaceDataset
from ourney_travelsync.recommenders import hybrid as h
from ourney_travelsync.recommenders.pipeline import prepare_group, recommend_group


def catalogue():
    records = tuple(dict(place_id=pid, place_name=pid, category_name='카페',
                         description_summary='stored text', description_source_url='https://source.example/poi',
                         detail_verification_status='PARTIAL') for pid in ('d', 'a', 'c', 'b'))
    x = np.eye(10)[:4]; x[2, 0] = 1
    return PlaceDataset(records, tuple(r['place_id'] for r in records), x, np.zeros((4, 2)))


def fake_encode(texts, config):
    rng = np.random.default_rng(20261105)
    a = rng.normal(size=(len(texts)+10, 384)).astype('float32')
    a /= np.linalg.norm(a, axis=1, keepdims=True)
    return a[:len(texts)], a[len(texts):]


@pytest.fixture
def config(tmp_path, monkeypatch):
    h._cached_embeddings.cache_clear()
    monkeypatch.setattr(h, '_encode', fake_encode)
    return h.ScoringConfig(cache_dir=str(tmp_path), regenerate=True)


def test_frozen_reference(config):
    fixture = json.loads((Path(__file__).parent/'fixtures/e5_reference.json').read_text(encoding='utf-8'))
    result = h.score_catalogue(catalogue(), np.array(fixture['users']), config=config)
    for key, attr in [('feature', 'feature_raw'), ('semantic', 'semantic_raw'),
                      ('feature_normalized', 'feature_normalized'), ('semantic_normalized', 'semantic_normalized'),
                      ('hybrid', 'hybrid')]:
        np.testing.assert_allclose(getattr(result, attr), fixture[key], atol=1e-14, rtol=1e-14)
    assert fixture['source_commit'] == '29018ce'


@pytest.mark.parametrize('alpha', [0, .5, 1])
def test_fusion_and_filtering_universe(config, alpha):
    P = catalogue(); users = {'a': dict(zip(FEATURE_NAMES, [5,1,4,2,3,3,3,3,3,3])),
                              'b': dict(zip(FEATURE_NAMES, [1,5,2,4,3,3,3,3,3,3]))}
    config = replace(config, alpha=alpha)
    full = prepare_group(P, users, scoring_config=config)
    filtered = prepare_group(P, users, eligible_place_ids=['a', 'd'], excluded_place_ids=['d'], scoring_config=config)
    np.testing.assert_equal(filtered.individual_scores, full.individual_scores[:, [full.poi_ids.index('a')]])
    expected = (1-alpha)*full.scoring.feature_normalized + alpha*full.scoring.semantic_normalized
    np.testing.assert_equal(full.individual_scores, expected)
    np.testing.assert_equal(GFAR().recommend(full), recommend_group(P, users, scoring_config=config))
    assert not np.array_equal(full.individual_scores[0], full.individual_scores[1])
    with pytest.raises(ValueError, match='AWM_REQUIRES_FEATURE_MEAN'):
        AverageWithoutMisery().recommend(full)


def test_positive_negative_neutral_and_mixed_fallback(config):
    users = np.array([[3]*10, [5]*10, [1]*10, [5]+[3]*9, [5,1]+[3]*8], float)
    result = h.score_catalogue(catalogue(), users, config=config)
    assert result.diagnostics['members'][0]['fallback_reason'] == 'all_neutral_preferences'
    np.testing.assert_equal(result.scores[0], .5)
    assert all(m['effective_scoring_method'] == 'e5_hybrid' for m in result.diagnostics['members'][1:])
    np.testing.assert_allclose(result.semantic_raw[1], -result.semantic_raw[2], atol=1e-14)
    assert np.isfinite(result.scores).all()
    assert result.components(0, 0)['hybrid_score'] is None


def test_failure_does_not_hide_invalid_input(config, monkeypatch):
    monkeypatch.setattr(h, 'embeddings', lambda *a: (_ for _ in ()).throw(h.SemanticUnavailable('semantic_model_unavailable')))
    result = h.score_catalogue(catalogue(), np.full((1,10), 5), config=config)
    assert result.diagnostics['members'][0]['fallback_reason'] == 'semantic_model_unavailable'
    np.testing.assert_equal(result.scores, result.feature_normalized)
    with pytest.raises(ValueError):
        h.score_catalogue(catalogue(), np.full((1,10), np.nan), config=config)
    with pytest.raises(ValueError):
        h.score_catalogue(catalogue(), np.full((1,10), 6), config=config)


def test_member_error_preserves_other_members(config, monkeypatch):
    original = h.semantic
    def fail_one(users, *args):
        if users[0, 0] == 1:
            raise RuntimeError('private diagnostic')
        return original(users, *args)
    monkeypatch.setattr(h, 'semantic', fail_one)
    result = h.score_catalogue(catalogue(), np.array([[1]*10, [5]*10]), config=config)
    assert [m['effective_scoring_method'] for m in result.diagnostics['members']] == ['feature_mean', 'e5_hybrid']
    assert 'private' not in json.dumps(result.diagnostics)


def test_normalization_contract():
    np.testing.assert_equal(h.normalize([[4,1,4,2]]), [[5/6,0,5/6,1/3]])
    np.testing.assert_equal(h.normalize([[7,7,7]]), [[.5,.5,.5]])
    np.testing.assert_equal(h.normalize([[7]]), [[.5]])
    assert h.normalize(np.empty((2,0))).shape == (2,0)


def test_exact_text_and_model_prefixes(config, monkeypatch):
    row = dict(place_name=' A ', category_name='카페', description_summary='source text', description_source_url='url')
    assert h.document(row) == ('A 카페 source text', True)
    assert h.document(row | {'detail_verification_status': 'CONFLICT'}) == ('A 카페', False)
    assert h.document(row | {'description_source_url': ''}) == ('A 카페', False)
    assert h.document({'enriched_metadata': row}) == h.document(row)
    calls = []
    class Model:
        def __init__(self, name, **kwargs): calls.append((name, kwargs))
        def encode(self, texts, **kwargs): calls.append((texts, kwargs)); return np.ones((len(texts),384))
    monkeypatch.setitem(sys.modules, 'sentence_transformers', types.SimpleNamespace(SentenceTransformer=Model))
    # Invoke the real loader rather than the fixture encoder.
    loader = _REAL_ENCODE
    loader(['actual POI'], config)
    assert calls[0][0] == h.MODEL_ID and calls[0][1]['revision'] == h.MODEL_REVISION
    assert calls[0][1]['device'] == 'cpu' and calls[0][1]['local_files_only'] is True
    assert calls[1][0] == ['query: '+s for s in h.LABELS]
    assert calls[2][0] == ['passage: actual POI']


_REAL_ENCODE = h._encode


def test_cache_alignment_invalidation_corruption_and_reuse(config, monkeypatch):
    calls = []
    def encode(*args): calls.append(1); return fake_encode(*args)
    monkeypatch.setattr(h, '_encode', encode)
    P = catalogue()
    first = h.embeddings(P, config)[0]
    reverse = replace(P, records=P.records[::-1], place_ids=P.place_ids[::-1], features=P.features[::-1], coordinates=P.coordinates[::-1])
    np.testing.assert_equal(h.embeddings(reverse, config)[0], first[::-1])
    assert len(calls) == 1
    path = next(Path(config.cache_dir).glob('*.npz'))
    h._cached_embeddings.cache_clear()
    np.testing.assert_equal(h.embeddings(P, config)[0], first)
    assert len(calls) == 1
    path.write_bytes(b'corrupted')
    h._cached_embeddings.cache_clear()
    np.testing.assert_equal(h.embeddings(P, config)[0], first)
    assert len(calls) == 2
    changed = replace(P, records=(P.records[0] | {'description_summary':'changed'}, *P.records[1:]))
    h.embeddings(changed, config)
    assert len(calls) == 3
    with pytest.raises(ValueError, match='ALIGNMENT'):
        h.embeddings(replace(P, place_ids=P.place_ids[::-1]), config)


def test_csv_and_database_join_have_same_cache_identity(config):
    P=catalogue()
    nested=replace(P,records=tuple(dict(place_id=r['place_id'],place_name=r['place_name'],category_name=r['category_name'],
                                       enriched_metadata={k:v for k,v in r.items() if k not in ('place_id','place_name','category_name')},
                                       dataset_version='pinned-version') for r in P.records))
    assert h.catalogue_identity(P)==h.catalogue_identity(nested)


def test_cache_race_and_missing_no_regeneration(config, monkeypatch):
    calls = []
    def encode(*args): calls.append(1); return fake_encode(*args)
    monkeypatch.setattr(h, '_encode', encode)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: h.embeddings(catalogue(), config)[0], range(4)))
    assert len(calls) == 1
    for a in results: np.testing.assert_equal(a, results[0])
    with pytest.raises(h.SemanticUnavailable, match='cache_unavailable'):
        h.embeddings(catalogue(), replace(config, cache_dir=str(Path(config.cache_dir)/'missing'), regenerate=False))


@pytest.mark.parametrize('kwargs', [{'alpha':-1}, {'alpha':float('nan')}, {'beta':-1}, {'beta':float('inf')}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError): h.ScoringConfig(**kwargs)


def test_feature_only_and_unclassified(config, monkeypatch):
    monkeypatch.setattr(h, 'embeddings', lambda *a: pytest.fail('feature path touched encoder'))
    P = catalogue(); users = np.array([[1]*10, [3]*10, [5]*10])
    result = h.score_catalogue(P, users, method='feature_mean', config=config)
    np.testing.assert_equal(result.scores, [[1]*4, [3]*4, [5]*4])
    with pytest.raises(ValueError, match='active feature'):
        h.score_catalogue(replace(P, features=np.zeros((4,10))), users, config=config)


def test_legacy_tfidf_cannot_enter_awm_directly():
    group=prepare_group(catalogue(),{'u':dict.fromkeys(FEATURE_NAMES,3)},individual_method='tfidf')
    with pytest.raises(ValueError,match='AWM_REQUIRES_FEATURE_MEAN'):
        AverageWithoutMisery(.25).recommend(group)


def test_nonfinite_embeddings_and_lock_timeout(config, monkeypatch):
    monkeypatch.setattr(h, '_encode', lambda texts, config: (np.full((len(texts),384), np.nan), np.eye(10,384)))
    result = h.score_catalogue(catalogue(), np.full((1,10),5), config=config)
    assert result.diagnostics['members'][0]['fallback_reason'] == 'semantic_invalid_model_output'
    ids, texts, meta = h.catalogue_identity(catalogue())
    (Path(config.cache_dir)/(h._digest(meta)+'.lock')).mkdir()
    result = h.score_catalogue(catalogue(), np.full((1,10),5), config=replace(config, lock_timeout=0))
    assert result.diagnostics['members'][0]['fallback_reason'] == 'semantic_cache_lock_timeout'
