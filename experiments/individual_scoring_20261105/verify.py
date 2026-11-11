"""Verify artifacts, guarded inputs, and optional deterministic offline rerun."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import csv

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
OUT=HERE/'results'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(name):
    with (OUT/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--rerun',action='store_true');a=parser.parse_args()
    names=['data_audit.json','field_missingness.csv','near_duplicate_review.csv','synthetic_profiles.csv',
        'structured_by_feature_count.csv','described_subset_controls.csv','individual_diagnostics.csv',
        'group_comparisons.csv','group_recommendations.csv','individual_top10.csv',
        'scale_mismatch_diagnostic.csv','labeling_template.csv','paired_description_ablation.csv',
        'normalization_sensitivity.csv','feature_count_bias.svg']
    before={n:sha(OUT/n) for n in names}
    if a.rerun:
        subprocess.run([sys.executable,str(HERE/'run.py')],cwd=ROOT,check=True)
        assert before=={n:sha(OUT/n) for n in names},'Nondeterministic result artifact'
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['successful_models']==2
    assert json.loads((OUT/'model_failures.json').read_text())==[]
    integrity=json.loads((OUT/'input_integrity.json').read_text())
    assert integrity['changed']==[]
    assert all(sha(ROOT/p)==h for p,h in integrity['before_sha256'].items())
    groups=read('group_comparisons.csv');rankings=read('group_recommendations.csv')
    assert len(groups)==1248 and len(read('individual_diagnostics.csv'))==104
    assert all(int(r['returned'])<=10 for r in groups if r['returned'])
    packet=read('labeling_template.csv')
    assert len(packet)==48 and len({r['place_id'] for r in packet})==48
    assert all(not r['relevance_1_5'] and not r['willingness_to_visit_yes_no'] for r in packet)
    assert sum(r['split']=='heldout' for r in packet)==24
    canonical=ROOT/'src/ourney_travelsync/data/travelsync_final_places_v2.csv'
    with canonical.open(encoding='utf-8-sig',newline='') as f:ids=sorted(r['place_id'] for r in csv.DictReader(f))
    assert not {ids[0],ids[10]} & {r['place_id'] for r in rankings}
    result=dict(checks='PASS',deterministic_rerun=a.rerun,stable_artifact_sha256=before,
                protected_input_count=len(integrity['before_sha256']),models_executed=2,
                scoring_configurations=104,group_rows=len(groups),actual_group_ranked_lists=len({(r['method'],r['group'],r['aggregation']) for r in rankings}),
                group_rows_by_status={s:sum(r['status']==s for r in groups) for s in sorted({r['status'] for r in groups})},label_rows=48)
    (OUT/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='stable_artifact_sha256'},indent=2))

if __name__=='__main__':main()
