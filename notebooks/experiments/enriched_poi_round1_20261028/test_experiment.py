"""Focused implementation checks. Synthetic labels here test math, never evidence."""
import math
import unittest
import numpy as np
from evaluate import ndcg, pair_agreement, audit_ratings
from prepare import BASE_FIELDS, DEFAULT_OUT, EXPANDED_FIELDS, clean
from run import Lexical, add_ranks, text_for, verify_frozen


class ExperimentTests(unittest.TestCase):
    def test_tie_metrics_do_not_reward_id_fallback(self):
        rows = add_ranks([{"place_id": "b", "score": 5.0}, {"place_id": "a", "score": 5.0}])
        labels = {"a": 3, "b": 0}
        self.assertEqual([r["place_id"] for r in rows], ["a", "b"])
        self.assertAlmostEqual(ndcg(rows, labels, 2), (1 + 1 / math.log2(3)) / 2)
        self.assertEqual(ndcg(rows, labels, 2, False), 1.0)
        self.assertEqual(pair_agreement(rows, labels), (0.5, 1))

    def test_undefined_metrics(self):
        rows = add_ranks([{"place_id": "a", "score": 0}, {"place_id": "b", "score": 0}])
        self.assertIsNone(ndcg(rows, {"a": 0, "b": 0}, 2))
        self.assertEqual(pair_agreement(rows, {"a": 1, "b": 1}), (None, 0))

    def test_correct_and_reversed_pair(self):
        rows = add_ranks([{"place_id": "a", "score": 2}, {"place_id": "b", "score": 1}])
        self.assertEqual(pair_agreement(rows, {"a": 3, "b": 0}), (1.0, 1))
        self.assertEqual(pair_agreement(rows, {"a": 0, "b": 3}), (0.0, 1))

    def test_zero_query_is_finite(self):
        model = Lexical(["떡볶이", "감자탕"])
        self.assertTrue(np.array_equal(model.encode(["", "xyz"]), np.zeros((2, len(model.vocab)))))

    def test_explicit_text_allowlist(self):
        row = {f: "NULL" for f in EXPANDED_FIELDS}
        row.update({"representative_menu": "떡볶이", "candidate_type": "LEAK", "user_A_score": "SECRET"})
        self.assertEqual(text_for(row, BASE_FIELDS), "떡볶이")
        self.assertEqual(clean(" N/A "), "")
        with self.assertRaises(ValueError):
            text_for(row, ["candidate_type"])

    def test_blank_annotations_rejected_as_human_labels(self):
        path = DEFAULT_OUT / "results/annotation_R1.csv"
        if not path.exists():
            self.skipTest("Run experiment first")
        with self.assertRaisesRegex(AssertionError, "explicit evidence status"):
            audit_ratings(path, path, "R1")

    def test_frozen_sources_preserved(self):
        verify_frozen(DEFAULT_OUT)


if __name__ == "__main__":
    unittest.main()
