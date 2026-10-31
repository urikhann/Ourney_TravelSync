"""Executed-artifact regression tests; requires the completed canonical run02."""
import csv,hashlib,json,unittest
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[3]
OLD=ROOT/'data/generated/evaluation/c_gfar_20261030_run01'
OUT=ROOT/'data/generated/evaluation/experiment4_maxmin_hybrid_20261030_run02'
def read(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def js(p):return json.loads(p.read_text(encoding='utf-8'))

@unittest.skipUnless((OUT/'REPORT.md').exists(),'Run the experiment first; this is an artifact regression suite')
class FrozenControls(unittest.TestCase):
    def test_frozen_groups(self):
        g=js(OUT/'group_profiles.json');self.assertEqual(g,js(OLD/'group_profiles.json'));self.assertEqual(len(g),105)
        self.assertEqual({r['size'] for r in g},{2,3,4,6,10});self.assertEqual(len({r['type'] for r in g}),7)
    def test_masks_identity_and_nested(self):
        masks=js(OUT/'description_masks.json');self.assertEqual(masks,js(OLD/'description_masks.json'))
        self.assertEqual({r['seed'] for r in masks},{11,29,47,71,101})
        for seed in [11,29,47,71,101]:
            prior=set()
            for r in sorted([r for r in masks if r['seed']==seed],key=lambda r:r['requested_percent']):
                current=set(r['masked_place_ids']);self.assertTrue(prior<=current);prior=current
    def test_dataset_identity(self):
        freeze=js(OUT/'pre_execution_freeze.json')
        for p,h in freeze['source_hashes'].items():self.assertEqual(hashlib.sha256((ROOT/p).read_bytes()).hexdigest(),h)
        self.assertEqual(len(read(OLD/'poi_index.csv')),3568)
    def test_historical_means(self):
        values={'C+AVG':(.5152471898807898,.6508151137398925),'C+AWM':(.4308899697545489,.5140051869761961),'C+GFAR':(.47556506988089453,.6400268805722927)}
        for r in read(OUT/'primary_summary.csv'):
            if r['assessor']=='C_rank' and r['method'] in values:
                a,b=values[r['method']];self.assertAlmostEqual(float(r['mean_minimum']),a,places=12);self.assertAlmostEqual(float(r['mean_member']),b,places=12)
    def test_baseline_all_rankings(self):
        r=read(OUT/'baseline_regression.csv');self.assertEqual(len(r),1050)
        self.assertTrue(all(x['ranking_identical']=='True' and x['metrics_identical_tolerance_1e12']=='True' for x in r))
    def test_lambda_zero_all_conditions(self):
        rows=read(OUT/'per_group_results.csv');index={}
        fields=['group_id','stage','assessor','K','L','optimization_input','mask_id','policy']
        for r in rows:
            if r['method']=='C+Max-Min':index[tuple(r[f] for f in fields)]=r
        count=0
        for r in rows:
            if r['method']=='C+Hybrid(0.00)':
                expected=index[tuple(r[f] for f in fields)];self.assertEqual(r['topk_ids'],expected['topk_ids']);self.assertEqual(r['member_utilities_json'],expected['member_utilities_json']);count+=1
        self.assertGreater(count,5000)
    def test_labels_and_production_preserved(self):
        frozen=js(OUT/'pre_execution_freeze.json')['protected_before']
        paths=[p for p in frozen if 'validated_adapter_' in p or 'annotation_R' in p or p.replace('\\','/').startswith(('src/','services/'))]
        self.assertTrue(any(p.replace('\\','/').startswith('src/') for p in paths))
        self.assertTrue(any('validated_adapter_R2.csv' in p for p in paths))
        for p in paths:self.assertEqual(hashlib.sha256((ROOT/p).read_bytes()).hexdigest(),frozen[p])
    def test_exact_controls(self):
        rows=read(OUT/'exact_controls.csv');self.assertEqual(len(rows),25)
        self.assertTrue(all(float(r['absolute_gap'])>=0 for r in rows))
        self.assertGreater(max(float(r['absolute_gap']) for r in rows),.1)
        self.assertTrue(all(float(r['absolute_gap'])<1e-12 for r in rows if float(r['lambda'])==1))

if __name__=='__main__':unittest.main()
