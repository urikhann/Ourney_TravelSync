import numpy as np
import pytest
from ourney_travelsync import GroupInput, load_places, FEATURE_NAMES
from ourney_travelsync.places import PlaceDataset
from ourney_travelsync.recommenders import generate_candidates, recommend_group
from ourney_travelsync.recommenders.ties import ranking


def test_score_order_overrides_hash_order():
    assert list(ranking([1, 5, 3], ('a','b','c'), 123)) == [1, 2, 0]


@pytest.mark.parametrize('seed', [0, 19])
def test_v2_reordered_rows_preserve_candidates_and_final_results(seed):
    places = load_places()
    rev = PlaceDataset(places.records[::-1], places.place_ids[::-1], places.features[::-1], places.coordinates[::-1])
    for values in ([3]*10, [5,1,4,2,3,1,5,2,4,1]):
        users = {'u': dict(zip(FEATURE_NAMES, values))}
        assert recommend_group(places, users, tie_seed=seed) == recommend_group(rev, users, tie_seed=seed)


def test_cutoff_and_seed_variation():
    data = GroupInput(('u',), tuple(map(str, range(50))), np.full((1,10),3.),
                      np.ones((50,10)), np.full((1,50),3.))
    a = generate_candidates(data, individual_top_n=10, tie_seed=1)
    b = generate_candidates(data, individual_top_n=10, tie_seed=2)
    assert set(a.poi_ids) != set(b.poi_ids)
    assert len(a.poi_ids) == 10
    np.testing.assert_equal(a.individual_scores, np.full((1,10),3.))
    with pytest.raises(ValueError): generate_candidates(data, tie_seed=True)
