import unittest
import numpy as np
from evaluate_metadata import SparseLexical,Lexical,sparse_scores,old_backend_control,stability,policy_scores


class MetadataTests(unittest.TestCase):
    def test_sparse_matches_dense_on_unseen_and_empty_queries(self):
        docs=['떡볶이 떡볶이 순대','감자탕 목뼈해장국','','숲 산책로 산책로']
        q=['떡볶이를 먹고 싶어요','편백 숲 산책로','xyz','']
        a,b=SparseLexical(docs),Lexical(docs)
        self.assertEqual(a.vocab,b.vocab)
        np.testing.assert_array_equal(a.idf,b.idf)
        np.testing.assert_allclose(sparse_scores(a.encode(q),a.encode(docs)),b.encode(q)@b.encode(docs).T,rtol=0,atol=1e-12)
        self.assertTrue(np.isfinite(sparse_scores(a.encode(q),a.encode(docs))).all())

    def test_historical_score_and_exact_tie_partitions_are_preserved(self):
        result=old_backend_control()
        self.assertTrue(all(r['all_orderings_and_exact_tie_partitions_identical'] for r in result.values()))

    def test_all_tied_stability_is_not_correlation_evidence(self):
        r=stability(np.zeros(3),np.zeros(3),['a','b','c'],k=2)
        self.assertEqual(r['spearman_average_tie_ranks'],'N/A')
        self.assertAlmostEqual(r['topk_membership_probability_overlap'],1)

    def test_abstention_and_lexicographic_control(self):
        b=np.array([.2,.1,.1]);c=np.array([0.,100.,-100.])
        p=policy_scores('B_backbone_C_ties',b,c,np.ones(3,dtype=bool))
        self.assertGreater(p[0],p[1])
        self.assertGreater(p[1],p[2])
        q=policy_scores('C_described_only',b,c,np.array([True,False,False]))
        self.assertEqual(np.isfinite(q).sum(),1)


if __name__=='__main__':
    unittest.main()
