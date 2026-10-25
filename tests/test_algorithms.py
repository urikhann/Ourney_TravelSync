import numpy as np
import pytest
from ourney_travelsync import GroupInput, Recommendation, AverageWithoutMisery, GFAR
from ourney_travelsync.evaluation import evaluate_satisfaction


def group():
    return GroupInput(('u1', 'u2'), ('a', 'b', 'c'), np.full((2, 10), 3.),
                      np.eye(10)[:3], np.array([[5., 3., 1.], [1., 3., 5.]]))


def test_awm_hand_calculated_veto_and_mean():
    assert AverageWithoutMisery(2.5).recommend(group()) == [Recommendation('b', 3.)]
    assert AverageWithoutMisery(1).recommend(group(), top_k=2) == [Recommendation('a', 3.), Recommendation('b', 3.)]


def test_gfar_hand_calculated_coverage():
    # Borda: u1=[2/3,1/3,0], u2=[0,1/3,2/3]. Initial gains tie.
    # a first by input order, then c: 2/3, then b: 2/9.
    results = GFAR(3).recommend(group(), top_k=3)
    assert [r.poi_id for r in results] == ['a', 'c', 'b']
    assert [r.score for r in results] == pytest.approx([2/3, 2/3, 2/9])


def test_evaluation_member_mean_and_empty():
    metrics = evaluate_satisfaction(group(), [Recommendation('a', 999), Recommendation('b', -1)])
    assert metrics == dict(returned_pois=2, mean_satisfaction=3., minimum_member_satisfaction=2.,
                           member_satisfaction={'u1': 4., 'u2': 2.})
    assert evaluate_satisfaction(group(), [])['mean_satisfaction'] is None
    for results in ([Recommendation('a', 0)] * 2, [Recommendation('unknown', 0)]):
        with pytest.raises(ValueError):
            evaluate_satisfaction(group(), results)
