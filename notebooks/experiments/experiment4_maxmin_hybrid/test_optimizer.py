import json,unittest
from pathlib import Path
import numpy as np
from optimizer import greedy,exact,ideal,objective,discounts

class OptimizerTests(unittest.TestCase):
    def setUp(self):
        self.x=np.array([[1.,0.,.6,.2],[0.,1.,.6,.2]])
        self.ids=list('abcd');self.users=['u1','u2'];self.z=ideal(self.x,2)
    def run_g(self,**kwargs):
        return greedy(self.x,self.ids,self.users,2,self.z,**kwargs)
    def test_objective(self):
        self.assertEqual(objective([.2,.8],0),.2)
        self.assertEqual(objective([.2,.8],.5),.35)
        self.assertEqual(objective([.2,.8],1),.5)
    def test_first_choice_mean_and_worst(self):self.assertEqual(self.run_g()[0][0],'c')
    def test_lambda_zero(self):self.assertEqual(self.run_g(),self.run_g(lam=0))
    def test_lambda_one_mean(self):
        picked,_,_=self.run_g(lam=1)
        expected=sorted(self.ids,key=lambda p:(-np.mean(self.x[:,self.ids.index(p)]/self.z),p))[:2]
        self.assertEqual(picked,expected)
    def test_member_order(self):
        a=self.run_g();b=greedy(self.x[::-1],self.ids,self.users[::-1],2,self.z[::-1]);self.assertEqual(a,b)
    def test_candidate_order(self):
        self.assertEqual(self.run_g(),greedy(self.x[:,::-1],self.ids[::-1],self.users,2,self.z))
    def test_trace_repeat(self):self.assertEqual(self.run_g(),self.run_g())
    def test_ties(self):self.assertEqual(greedy(np.ones((2,3)),list('cba'),self.users,2,[2,2])[0],['a','b'])
    def test_unique_full_length(self):
        p,_,info=greedy(self.x,self.ids,self.users,6,ideal(self.x,6));self.assertEqual(len(p),4);self.assertEqual(len(set(p)),4);self.assertEqual(info['shortage'],2)
    def test_empty(self):
        p,t,info=greedy(np.zeros((2,0)),[],self.users,2,[1,1]);self.assertEqual(p,[]);self.assertEqual(info['status'],'CANDIDATE_SHORTAGE')
    def test_k_zero(self):self.assertEqual(greedy(self.x,self.ids,self.users,0,[1,1])[0],[])
    def test_undefined(self):self.assertEqual(greedy(self.x,self.ids,self.users,2,[0,1])[2]['status'],'UNDEFINED_NORMALIZER')
    def test_invalid_relevance(self):
        for value in [-1,np.inf,np.nan]:
            x=self.x.copy();x[0,0]=value
            with self.assertRaises(ValueError):greedy(x,self.ids,self.users,2,self.z)
    def test_bad_normalizer(self):
        for z in [[-1,1],[np.nan,1],[1]]:
            with self.assertRaises(ValueError):greedy(self.x,self.ids,self.users,2,z)
    def test_invalid_lambda(self):
        for l in [-.1,1.1,np.nan]:
            with self.assertRaises(ValueError):self.run_g(lam=l)
    def test_invalid_k(self):
        for k in [-1,True,1.2]:
            with self.assertRaises(ValueError):discounts(k)
    def test_exclusions(self):
        self.assertNotIn('c',self.run_g(excluded=['c'])[0])
    def test_positional_fixed_denominator(self):
        p,t,info=self.run_g();cols=[self.ids.index(i) for i in p]
        np.testing.assert_allclose(info['member_utilities'],(self.x[:,cols]@discounts(2))/self.z)
        np.testing.assert_allclose(t[0]['member_utilities_after'],self.x[:,cols[0]]/self.z)
    def test_exact_known(self):
        p,v=exact([[1,0],[0,1]],['a','b'],self.users,2,[1,1]);self.assertEqual(p,['a','b']);self.assertAlmostEqual(v,1/np.log2(3))
    def test_exact_bounds_greedy(self):
        gaps=[]
        for seed in range(10):
            x=np.random.default_rng(seed).random((3,6));z=ideal(x,3)
            for l in [0,.25,.5,.75,1]:
                _,v=exact(x,list('abcdef'),['a','b','c'],3,z,l)
                _,_,g=greedy(x,list('abcdef'),['a','b','c'],3,z,l)
                self.assertGreaterEqual(v+1e-11,g['objective']);gaps.append(v-g['objective'])
        self.assertGreater(max(gaps),.001)
    def test_mean_exact_equivalence(self):
        for seed in range(5):
            x=np.random.default_rng(seed).random((3,6));z=ideal(x,3)
            self.assertEqual(exact(x,list('abcdef'),['a','b','c'],3,z,1)[0],greedy(x,list('abcdef'),['a','b','c'],3,z,1)[0])
    def test_exact_guard(self):
        with self.assertRaises(ValueError):exact(np.ones((2,10)),list('abcdefghij'),self.users,2,[1,1])

if __name__=='__main__':unittest.main()
