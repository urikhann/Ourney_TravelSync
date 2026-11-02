"""Independent metric checks, including exact ties crossing cutoffs."""
import itertools
import math

import numpy as np
import pytest
from sklearn.metrics import ndcg_score

from evaluate_tfidf import expected_reciprocal_rank, metric_values


def test_tied_metrics_match_exhaustive_permutations():
    ids = ('a', 'b', 'c', 'd')
    labels = dict(zip(ids, (3, 0, 2, 1)))
    scores = [1.0] * 4
    for k in (1, 2, 5, 10):
        result = metric_values(ids, scores, labels, k)
        ndcgs, precisions, recalls, reciprocal_ranks = [], [], [], []
        gains = [2**labels[pid]-1 for pid in ids]
        ideal = sum(g/math.log2(i+2) for i,g in enumerate(sorted(gains,reverse=True)[:k]))
        for order in itertools.permutations(ids):
            hits = sum(labels[pid]>=2 for pid in order[:k])
            ndcgs.append(sum((2**labels[pid]-1)/math.log2(i+2) for i,pid in enumerate(order[:k]))/ideal)
            precisions.append(hits/k)
            recalls.append(hits/2)
            reciprocal_ranks.append(1/next(i for i,pid in enumerate(order,1) if labels[pid]>=2))
        for key, values in [('ndcg',ndcgs),('precision',precisions),('recall',recalls),('mrr',reciprocal_ranks)]:
            assert result[key] == pytest.approx(np.mean(values), abs=1e-14)


def test_ndcg_legacy_function_matches_sklearn_tie_oracle():
    ids = ('a','b','c','d','e','f')
    labels = dict(zip(ids,(3,0,2,1,3,0)))
    scores = [1,.5,.5,.5,0,0]
    for k in (5,10):
        actual=metric_values(ids,scores,labels,k)['ndcg']
        expected=ndcg_score([[2**labels[pid]-1 for pid in ids]],[scores],k=k,ignore_ties=False)
        assert actual == pytest.approx(expected)


def test_mrr_tied_band_after_nonrelevant_prefix():
    assert expected_reciprocal_rank([2,1,1,1],[False,True,False,False]) == pytest.approx((1/2+1/3+1/4)/3)
    assert expected_reciprocal_rank([2,1],[False,False]) is None


def test_short_pool_no_relevance_and_unknown_labels():
    result=metric_values(('a','b'),[1,0],{'a':0,'b':0},10)
    assert result['ndcg'] is None and result['recall'] is None and result['mrr'] is None
    assert result['precision']==0 and result['returned_count']==2
    result=metric_values(('a','b'),[1,0],{'a':3,'b':0},10)
    assert result['precision']==.1 and result['recall']==1
    with pytest.raises(ValueError,match='completely judged'):
        metric_values(('a','b'),[1,0],{'a':3},5)
    with pytest.raises(ValueError,match='completely judged'):
        metric_values(('a','b'),[1,0],{'a':3,'b':None},5)


def test_query_and_label_canaries_never_enter_fit(monkeypatch):
    from ourney_travelsync.recommenders import tfidf
    original = tfidf.TfidfVectorizer.fit_transform
    captured=[]
    def spy(self, raw_documents, *args, **kwargs):
        captured.extend(raw_documents)
        return original(self,raw_documents,*args,**kwargs)
    monkeypatch.setattr(tfidf.TfidfVectorizer,'fit_transform',spy)
    a=tfidf.score_tfidf(['QUERY_CANARY'],[{'description_summary':'바다 산책','relevance_0_3':'LABEL_CANARY'}])
    assert captured==['바다 산책']
    assert not a.any()
