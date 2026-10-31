import importlib.util
from pathlib import Path
import unittest
import numpy as np
from aggregation import aggregate,calibrate,borda,expected_rank_relevance,utilities,utility_metrics,policy_backbone
from experiment import mask_scores,validate_bundle,make_groups,ROOT


def fixture():
    return np.array([[5.,3.,0.,1.],[5.,3.,0.,1.],[0.,3.,5.,1.]]),['A','B','C','D'],['u1','u2','u3']


class GroupTests(unittest.TestCase):
    def test_sdd_fixture_A_C_B(self):
        x,ids,users=fixture();order,tr,_=aggregate(x,calibrate(x),ids,users,'GFAR',3,3)
        self.assertEqual(order,['A','C','B']);np.testing.assert_allclose([t['selection_gain'] for t in tr],[4/3,2/3,1/3])

    def test_paper_table2_reference(self):
        x=np.array([[5,4.5,4,4,.5],[5,4.5,4,1.5,3],[2.5,2.5,3,5,1]])
        picked,tr,_=aggregate(x,calibrate(x),['i1','i2','i3','i4','i5'],['u1','u2','u3'],'GFAR',3,3)
        # Eq.5 gives 2/9 at the third step. Extracted paper Table2 says 2/27;
        # preserve/report that discrepancy, not a different recurrence.
        self.assertEqual(picked,['i1','i4','i2']);np.testing.assert_allclose([r['selection_gain'] for r in tr],[4/3,2/3,2/9])

    def test_sdd_reference_module_equivalence(self):
        s=importlib.util.spec_from_file_location('sdd_reference',ROOT/'services/mocks/group_rec.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
        x,ids,users=fixture();scaled=calibrate(x)
        tops={u:[ids[i] for i in sorted(range(4),key=lambda i:(-x[j,i],ids[i]))[:3]] for j,u in enumerate(users)}
        reference=m.gfar(tops,3,{p:float(scaled[:,i].mean()) for i,p in enumerate(ids)})
        self.assertEqual(aggregate(x,scaled,ids,users,'GFAR',3,3)[0],[r['poi_id'] for r in reference])

    def test_member_permutation(self):
        x,ids,users=fixture();p=[2,0,1]
        self.assertEqual(aggregate(x,calibrate(x),ids,users)[0],aggregate(x[p],calibrate(x[p]),ids,[users[i] for i in p])[0])

    def test_candidate_permutation(self):
        x,ids,users=fixture();p=[3,1,0,2]
        self.assertEqual(aggregate(x,calibrate(x),ids,users)[0],aggregate(x[:,p],calibrate(x[:,p]),[ids[i] for i in p],users)[0])

    def test_no_duplicates_and_topk_bound(self):
        x,ids,users=fixture();p=aggregate(x,calibrate(x),ids,users,k=99)[0]
        self.assertEqual(len(p),4);self.assertEqual(len(set(p)),4)

    def test_positive_affine_score_scale_invariance(self):
        x,ids,users=fixture();y=x*7+13
        for cal in ['percentile','minmax']:
            np.testing.assert_allclose(calibrate(x,cal),calibrate(y,cal))
            for alg in ['AVG','AWM','GFAR']:self.assertEqual(aggregate(x,calibrate(x,cal),ids,users,alg)[0],aggregate(y,calibrate(y,cal),ids,users,alg)[0])

    def test_deterministic_ID_ties(self):
        x=np.ones((2,3));p=aggregate(x,calibrate(x),['c','a','b'],['u1','u2'],'AVG',2)[0]
        self.assertEqual(p,['a','b'])

    def test_metadata_mask_projection_only(self):
        x=np.array([[.2,.6]]);p=np.array([[0.,.1]])
        np.testing.assert_array_equal(mask_scores(x,p,[False,True]),[[.2,.1]])

    def test_missing_or_nonfinite_relevance_rejected(self):
        for x in [np.array([[np.nan]]),np.array([[np.inf]]),np.empty((0,1))]:
            with self.assertRaises(ValueError):aggregate(x,x,['a'],['u'])

    def test_explicit_hard_filter_before_aggregation(self):
        x=np.array([[5,1],[5,1]],dtype=float);allowed=[False,True]
        self.assertEqual(aggregate(x[:,allowed],calibrate(x)[:,allowed],['safe'],['u1','u2'],k=3)[0],['safe'])

    def test_single_member(self):
        x=np.array([[1,3,2]],dtype=float)
        self.assertEqual(aggregate(x,calibrate(x),['a','b','c'],['u'],k=3)[0],['b','c','a'])

    def test_identical_members(self):
        x=np.array([[3,2,1],[3,2,1]],dtype=float)
        self.assertEqual(aggregate(x,calibrate(x),['a','b','c'],['u1','u2'],k=3,depth=3)[0],['a','b','c'])

    def test_strong_conflict_minority_gets_early_item(self):
        x,ids,users=fixture();picked=aggregate(x,calibrate(x),ids,users,k=2,depth=3)[0]
        self.assertEqual(picked,['A','C'])

    def test_AWM_threshold_is_inclusive(self):
        x=np.array([[.5,.49],[.5,.8]])
        self.assertEqual(aggregate(x,x,['a','b'],['u1','u2'],'AWM',2,threshold=.5)[0],['a'])

    def test_AWM_does_not_fill_empty(self):
        x=np.array([[1.,0.],[0.,1.]])
        self.assertEqual(aggregate(x,x,['a','b'],['u1','u2'],'AWM',2,threshold=.5)[0],[])

    def test_empty_pool_and_Kzero(self):
        for alg in ['AVG','AWM','GFAR']:
            self.assertEqual(aggregate(np.empty((2,0)),np.empty((2,0)),[],['u1','u2'],alg)[0],[])
            self.assertEqual(aggregate(np.ones((2,1)),np.ones((2,1)),['a'],['u1','u2'],alg,0)[0],[])

    def test_invalid_topk_and_identity(self):
        for k in [-1,True,1.5]:
            with self.assertRaises(ValueError):aggregate(np.ones((1,1)),np.ones((1,1)),['a'],['u'],k=k)
        with self.assertRaises(ValueError):aggregate(np.ones((1,2)),np.ones((1,2)),['a','a'],['u'])

    def test_version_and_alignment_consistency(self):
        with self.assertRaises(ValueError):validate_bundle({'C':np.ones((1,2))},['S01'],['a','b'],'old','new')
        with self.assertRaises(ValueError):validate_bundle({'C':np.ones((2,2))},['S01'],['a','b'],'x','x')

    def test_trace_unmet_update_and_gain(self):
        x,ids,users=fixture();_,trace,_=aggregate(x,calibrate(x),ids,users,k=3,depth=3)
        for t in trace:
            p,q,a=map(np.asarray,[t['member_relevance'],t['unmet_before'],t['unmet_after']])
            np.testing.assert_allclose(a,q*(1-p));self.assertAlmostEqual(t['selection_gain'],float(q@p))

    def test_tied_evaluator_has_no_ID_privilege(self):
        e=expected_rank_relevance(np.ones((1,4)),support=2)
        np.testing.assert_array_equal(e,np.full((1,4),.375))

    def test_empty_and_zero_utility_not_fairness_one(self):
        self.assertEqual(utilities(np.ones((2,2)),[],2),[None,None])
        self.assertIsNone(utility_metrics([0.,0.])['jain'])
        self.assertIsNone(utility_metrics([None,None])['mean_utility'])

    def test_short_lists_keep_fixed_ideal(self):
        e=np.array([[1.,1.]])
        self.assertLess(utilities(e,[0],2)[0],utilities(e,[0,1],2)[0])

    def test_group_profiles_all_types_sizes_seeds(self):
        g=make_groups();self.assertEqual(len(g),105);self.assertEqual({x['size'] for x in g},{2,3,4,6,10})
        self.assertEqual(g,make_groups());self.assertTrue(any(x['excluded_features'] for x in g))

    def test_backbone_cannot_override_strict_B_order(self):
        p=policy_backbone([.2,.1],[0,1000]);self.assertGreater(p[0],p[1])


if __name__=='__main__':unittest.main()
