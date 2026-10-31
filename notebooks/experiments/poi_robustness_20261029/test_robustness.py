"""Metric/coverage edge cases; synthetic labels here are never evaluation data."""
import unittest
import numpy as np
from run_robustness import rank_info, stability, make_masks, policy_scores, verify_original_content


class RobustnessTests(unittest.TestCase):
    def test_original_content_freeze_with_known_analysis_additions(self):
        self.assertIn(verify_original_content()['legacy_exact_snapshot'],['PASS','REJECTS_KNOWN_TRACKED_ADDITIONS'])

    def test_all_tied_cutoff_is_probability_not_id_preference(self):
        ids=['a','b','c','d']
        r=rank_info(np.zeros(4),ids,k=2)
        self.assertEqual(r['ties'],6)
        self.assertEqual(r['tie_rate'],1)
        np.testing.assert_array_equal(r['probability'],[.5]*4)
        s=stability(np.zeros(4),np.zeros(4),ids,k=2)
        self.assertEqual(s['topk_membership_probability_overlap'],1)
        self.assertEqual(s['spearman_average_tie_ranks'],'N/A')

    def test_reversal_and_complete_loss(self):
        s=stability(np.array([3.,2.,1.]),np.array([1.,2.,3.]),['a','b','c'],k=1)
        self.assertAlmostEqual(s['spearman_average_tie_ranks'],-1)
        self.assertEqual(s['topk_id_retention'],0)
        s=stability(np.array([3.,2.,1.]),np.array([np.nan]*3),['a','b','c'],k=2)
        self.assertEqual(s['topk_id_retention'],0)
        self.assertEqual(s['topk_returned'],0)
        self.assertEqual(s['spearman_average_tie_ranks'],'N/A')

    def test_masks_nested_and_only_available_descriptions(self):
        ids=[f'p{i}' for i in range(33)]
        masks=make_masks(ids)
        self.assertEqual(len(masks),21)
        for m in masks:
            self.assertTrue(set(m['masked_place_ids'])<=set(ids))
            self.assertEqual(m['masked_count'],int(33*m['requested_percent']/100+.5))
        for seed in [11,29,47,71,101]:
            selected=[m for m in masks if m['seed']==seed]
            for a,b in zip(selected,selected[1:]):
                self.assertTrue(set(a['masked_place_ids'])<=set(b['masked_place_ids']))

    def test_lexicographic_fallback_never_overrides_strict_metadata_order(self):
        b=np.array([.2,.1,.1])
        c=np.array([0.,999.,-999.])
        scores=policy_scores('B_backbone_C_ties',b,c,np.ones(3,dtype=bool))
        self.assertGreater(scores[0],scores[1])
        self.assertGreater(scores[1],scores[2])
        np.testing.assert_array_equal(policy_scores('B_global',b,c,np.zeros(3,dtype=bool)),b)

    def test_abstention_keeps_identity_denominator(self):
        b=np.array([.2,.1,.1]); c=np.array([1.,0.,.3])
        observed=policy_scores('C_described_only',b,c,np.array([True,False,False]))
        self.assertEqual(len(observed),3)
        self.assertEqual(rank_info(observed,['a','b','c'])['rankable'],1)


if __name__=='__main__':
    unittest.main()
