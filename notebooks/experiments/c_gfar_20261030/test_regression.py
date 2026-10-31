"""Integration regression against existing production functions and frozen scores."""
import json,unittest
import numpy as np
from aggregation import aggregate,calibrate
from experiment import META,ROUND1,FEATURES,read_csv,digest,SparseLexical,sparse_scores,clean,score_individuals
from ourney_travelsync import GroupInput,AverageWithoutMisery,GFAR


class RegressionTests(unittest.TestCase):
    def test_production_AWM_boundary_equivalence(self):
        x=np.array([[.5,.49,.8],[.5,.8,.6]])
        data=GroupInput(('u1','u2'),('a','b','c'),np.full((2,10),3.),np.eye(10)[:3],x)
        expected=AverageWithoutMisery(.5).recommend(data,top_k=3)
        actual=aggregate(x,x,['a','b','c'],['u1','u2'],'AWM',3,threshold=.5)[0]
        self.assertEqual(actual,[r.poi_id for r in expected])

    def test_existing_production_GFAR_unambiguous_fixture(self):
        x=np.array([[5.,3.,0.,1.],[5.,3.,0.,1.],[0.,3.,5.,1.]])
        data=GroupInput(('u1','u2','u3'),('A','B','C','D'),np.full((3,10),3.),np.eye(10)[:4],x)
        self.assertEqual([r.poi_id for r in GFAR(3).recommend(data,top_k=3)],aggregate(x,calibrate(x),list(data.poi_ids),list(data.user_ids),'GFAR',3,3)[0])

    def test_historical_A_B_C_D_scoring_regression(self):
        artifacts=json.loads((META/'output_hashes.json').read_text(encoding='utf-8'))
        self.assertEqual(digest(META/'scores_enriched_catalogue.npz'),artifacts['scores_enriched_catalogue.npz'])
        cached=np.load(META/'scores_enriched_catalogue.npz',allow_pickle=False)
        docs=[r for r in read_csv(META/'model_inputs.csv') if r['domain']=='enriched_catalogue']
        canonical={r['place_id']:r for r in read_csv(META/'inputs/travelsync_poi_metadata_enriched_v1.csv')}
        profiles=json.loads((ROUND1/'scenarios.json').read_text(encoding='utf-8'))
        pref=np.asarray([[p['preference_vector'][f] for f in FEATURES] for p in profiles])
        features=np.asarray([[float(canonical[r['place_id']][f]) for f in FEATURES] for r in docs])
        np.testing.assert_array_equal(score_individuals(pref,features),cached['A'])
        specs=json.loads((META/'lexical_models.json').read_text(encoding='utf-8'))['enriched_catalogue']
        for method,field in [('B','B_text'),('C','C_D_text')]:
            model=object.__new__(SparseLexical);model.vocab=specs[method]['vocabulary'];model.index={v:i for i,v in enumerate(model.vocab)};model.idf=np.asarray(specs[method]['idf'])
            computed=sparse_scores(model.encode([clean(p['preference_ko']) for p in profiles]),model.encode([r[field] for r in docs]))
            np.testing.assert_allclose(computed,cached[method],rtol=0,atol=1e-12)
        semantic=json.loads((META/'semantic_metadata.json').read_text(encoding='utf-8'))
        self.assertTrue(semantic['repeat_embeddings_byte_identical'])
        self.assertEqual(cached['D'].shape,cached['C'].shape)
        self.assertTrue(np.isfinite(cached['D']).all())
        # D is a frozen-artifact/hash regression, not a newly executed encoder test.


if __name__=='__main__':unittest.main()
