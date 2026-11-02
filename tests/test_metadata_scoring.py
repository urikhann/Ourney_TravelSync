import numpy as np
import pytest

from ourney_travelsync.recommenders.tfidf import document, score_texts, taste_query
from ourney_travelsync.db.metadata_import import read_metadata_csv, validate_matches


def test_text_allowlist_missing_and_nested_metadata():
    assert document({'enriched_metadata': {'food_type': 'seafood', 'description_summary': 'quiet harbour',
                                          'place_name': 'forbidden', 'menu_raw': 'forbidden', 'activity_type': 'NULL'}}) == 'seafood quiet harbour'
    assert document({'description_summary': None}) == ''
    assert np.array_equal(score_texts([{}, {'food_type': 'seafood'}], ['', 'zzz']), np.zeros((2, 2)))
    assert score_texts([{}, {}], ['seafood']).shape == (1, 2)


def test_metadata_changes_scores_without_changing_features():
    scores = score_texts([{'food_type': 'seafood'}, {'food_type': 'bakery'}], ['seafood', 'bakery'])
    assert scores[0, 0] == pytest.approx(1) and scores[0, 1] == 0
    assert scores[1, 1] == pytest.approx(1) and scores[1, 0] == 0


def test_upstream_optional_tastes_supply_only_positive_keywords():
    assert taste_query({'keywords': ['seafood', 'coast'], 'avoid_keywords': ['hiking']}) == 'seafood coast'
    assert taste_query(None) == ''
    assert taste_query({'avoid_keywords': ['hiking']}) == ''
    with pytest.raises(ValueError):
        taste_query({'keywords': 'seafood'})
    with pytest.raises(ValueError):
        taste_query({'keywords': [], 'avoid_keywords': [None]})


def test_metadata_reader_rejects_duplicates_versions_and_count(tmp_path):
    file = tmp_path / 'metadata.csv'
    file.write_text('place_id,dataset_version\na,00000000-0000-0000-0000-000000000001\na,00000000-0000-0000-0000-000000000001\n')
    with pytest.raises(ValueError, match='DUPLICATE_METADATA_KEY'):
        read_metadata_csv(file, expected_count=2)
    with pytest.raises(ValueError, match='ROW_COUNT'):
        read_metadata_csv(file)
    rows = [{'place_id': 'a', 'dataset_version': 'v1'}]
    with pytest.raises(ValueError, match='DATASET_VERSION'):
        validate_matches(rows, [{'place_id': 'a'}], 'v2')
    with pytest.raises(ValueError, match='POI_SET'):
        validate_matches(rows, [{'place_id': 'b'}], 'v1')


def test_eligibility_before_text_fit():
    from ourney_travelsync.places import PlaceDataset
    from ourney_travelsync.features import FEATURE_NAMES
    from ourney_travelsync.recommenders.pipeline import prepare_group
    records = ({'food_type': 'seafood'}, {'food_type': 'seafood bakery'}, {'food_type': 'bakery'})
    features = np.zeros((3, 10)); features[:, 0] = 1
    places = PlaceDataset(records, ('a', 'b', 'c'), features, np.zeros((3, 2)))
    group = prepare_group(places, {'u': dict.fromkeys(FEATURE_NAMES, 3)}, excluded_place_ids=['b'],
                          individual_method='tfidf', preference_texts={'u': 'seafood'})
    expected = dict(zip(('a', 'c'), score_texts([records[0], records[2]], ['seafood'])[0]))
    assert dict(zip(group.poi_ids, group.individual_scores[0])) == expected
