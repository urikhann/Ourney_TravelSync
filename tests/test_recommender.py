"""Regression tests for the final CSV contract and recommendation flow."""

from collections import Counter
import csv
from pathlib import Path
import tempfile
import unittest

import numpy as np

# from ourney_travelsync import FeatureAGREE
from ourney_travelsync import FEATURE_NAMES, GFAR, AverageWithoutMisery, GroupInput, load_places
from ourney_travelsync.RecSys import (
    calculate_preference_score, generate_candidates, prepare_group,
    recommend_group, score_individuals,
)


def preferences(**overrides):
    return dict.fromkeys(FEATURE_NAMES, 3) | overrides


class ScoringTests(unittest.TestCase):
    def test_multilabel_score_stays_on_survey_scale(self):
        poi = dict.fromkeys(FEATURE_NAMES, 0) | {"food": 1, "cafe": 1}
        self.assertEqual(calculate_preference_score(preferences(food=5, cafe=1), poi), 3)
        self.assertEqual(calculate_preference_score(dict.fromkeys(FEATURE_NAMES, 5), poi), 5)
        self.assertEqual(poi["food"], 1)

    def test_mapping_order_is_irrelevant(self):
        poi = dict.fromkeys(reversed(FEATURE_NAMES), 0) | {"art": 1}
        self.assertEqual(calculate_preference_score(preferences(art=4), poi), 4)

    def test_invalid_features_and_preferences(self):
        valid_poi = dict.fromkeys(FEATURE_NAMES, 0) | {"cafe": 1}
        invalid_pois = [
            dict.fromkeys(FEATURE_NAMES, 0),
            valid_poi | {"food": 0.5}, valid_poi | {"food": -1},
            valid_poi | {"food": float("nan")},
            {"cafe": 1}, valid_poi | {"museum": 1},
        ]
        for poi in invalid_pois:
            with self.subTest(poi=poi), self.assertRaises(ValueError):
                calculate_preference_score(preferences(), poi)
        for user in [preferences(food=0), preferences(food=6), preferences(food=float("inf")), {"cafe": 5}]:
            with self.subTest(user=user), self.assertRaises(ValueError):
                calculate_preference_score(user, valid_poi)

    def test_batch_matches_scalar_scores_and_keeps_inputs(self):
        users = np.array([[5, 1] + [3] * 8, [1, 5] + [3] * 8], dtype=float)
        pois = np.eye(10)[:2]
        pois = np.vstack([pois, pois.sum(axis=0)])
        original = pois.copy()
        scores = score_individuals(users, pois)
        np.testing.assert_array_equal(scores, [[5, 1, 3], [1, 5, 3]])
        np.testing.assert_array_equal(pois, original)
        for u, user in enumerate(users):
            for p, poi in enumerate(pois):
                self.assertEqual(scores[u, p], calculate_preference_score(
                    dict(zip(FEATURE_NAMES, user)), dict(zip(FEATURE_NAMES, poi))))

    def test_group_contract_rejects_old_dimensions(self):
        with self.assertRaises(ValueError):
            GroupInput(("u",), ("p",), np.ones((1, 4)), np.ones((1, 4))).validate()
        # with self.assertRaises(ValueError):
        #     FeatureAGREE(4, 4)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.places = load_places()
        cls.users = {"food-lover": preferences(food=5, cafe=1), "cafe-lover": preferences(food=1, cafe=5)}

    def test_final_dataset(self):
        places = self.places
        self.assertEqual(len(places.place_ids), 3591)
        self.assertEqual(Counter(row["source"] for row in places.records),
                         {"KAKAO": 3101, "TOUR_API": 367, "TOUR_API+KAKAO": 123})
        self.assertEqual(places.features.shape, (3591, 10))
        self.assertTrue(np.isin(places.features, (0, 1)).all())
        self.assertTrue((places.features.sum(axis=1) > 0).all())
        self.assertTrue(all(row["feature_rules"] for row in places.records))

    def test_candidate_union_preserves_alignment(self):
        data = GroupInput(("u1", "u2"), ("p1", "p2", "p3"),
                          np.full((2, 10), 3.), np.eye(10)[:3],
                          np.array([[5., 1., 3.], [1., 5., 3.]]))
        result = generate_candidates(data, individual_top_n=1)
        self.assertEqual(result.poi_ids, ("p1", "p2"))
        np.testing.assert_array_equal(result.individual_scores, [[5, 1], [1, 5]])
        np.testing.assert_array_equal(result.poi_features, data.poi_features[:2])

    def test_candidate_ties_follow_original_order(self):
        data = GroupInput(("u",), ("b", "a"), np.full((1, 10), 3.),
                          np.eye(10)[:2], np.full((1, 2), 3.))
        self.assertEqual(generate_candidates(data, individual_top_n=1).poi_ids, ("b",))

    def test_constraints_apply_before_candidate_selection(self):
        eligible = self.places.place_ids[:5]
        excluded = eligible[:2]
        group = prepare_group(self.places, self.users, individual_top_n=100,
                              eligible_place_ids=eligible, excluded_place_ids=excluded)
        self.assertEqual(group.poi_ids, eligible[2:])
        np.testing.assert_array_equal(group.poi_features, self.places.features[2:5])

    def test_full_pipeline_gfar_and_awm(self):
        group = prepare_group(self.places, self.users, individual_top_n=30)
        self.assertLessEqual(len(group.poi_ids), 60)
        group.validate(require_scores=True)
        self.assertTrue(((group.individual_scores >= 1) & (group.individual_scores <= 5)).all())
        results = recommend_group(self.places, self.users, individual_top_n=30, top_k=10)
        self.assertEqual(len(results), 10)
        self.assertEqual(len({result.poi_id for result in results}), 10)
        self.assertTrue(all(result.poi_id in group.poi_ids for result in results))
        self.assertEqual(results, GFAR().recommend(group, top_k=10))
        self.assertEqual(recommend_group(self.places, self.users, individual_top_n=30,
                                        recommender=AverageWithoutMisery()),
                         AverageWithoutMisery().recommend(group))

    def test_empty_and_singleton_candidates(self):
        for kwargs in [{"eligible_place_ids": ()}, {"excluded_place_ids": self.places.place_ids}, {"individual_top_n": 0}]:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(recommend_group(self.places, self.users, **kwargs), [])
        result = recommend_group(self.places, self.users, eligible_place_ids=self.places.place_ids[:1])
        self.assertEqual([r.poi_id for r in result], list(self.places.place_ids[:1]))
        self.assertEqual(recommend_group(self.places, self.users, top_k=0), [])

    def test_invalid_group_and_limits(self):
        with self.assertRaises(ValueError):
            prepare_group(self.places, {})
        for limit in [-1, 1.5, True]:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                prepare_group(self.places, self.users, individual_top_n=limit)

    def test_csv_validation(self):
        row = self.places.records[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "places.csv"
            for rows in [[row, row], [row | {"food": "0.5"}], [row | {"latitude": "NaN"}],
                         [row | {"place_name": ""}], [row | dict.fromkeys(FEATURE_NAMES, "0")]]:
                with path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(row))
                    writer.writeheader()
                    writer.writerows(rows)
                with self.subTest(rows=rows), self.assertRaises(ValueError):
                    load_places(path)
            path.write_text("place_id,place_name\np,name\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_places(path)


if __name__ == "__main__":
    unittest.main()
