"""Independent output checks, test logs, and repeat comparison for metadata rerun."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import subprocess
import sys
import numpy as np

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from evaluate_metadata import (ROOT,PREV,ROUND1,read_csv,write_json,digest,verify_legacy_content)


def verify(out,reference):
    out,reference=out.resolve(),reference.resolve()
    manifest=json.loads((out/'output_hashes.json').read_text(encoding='utf-8'))
    assert all(digest(out/name)==v for name,v in manifest.items())
    code_manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    if (out/'completion_manifest.json').exists():
        completion=json.loads((out/'completion_manifest.json').read_text(encoding='utf-8'))
        assert all(digest(p)==v for p,v in completion['current_code_sha256'].items())
        assert digest(out/'code_at_scoring/evaluate_metadata.py')==code_manifest['code_hashes'][str(HERE/'evaluate_metadata.py')]
    else:
        assert all(digest(p)==v for p,v in code_manifest['code_hashes'].items()), 'Frozen code changed since this run'
    suites=[]
    for name,folder,pattern in [('new_metadata',HERE,'test_metadata.py'),('original_round1',HERE.parent/'enriched_poi_round1_20261028','test_experiment.py')]:
        command=[sys.executable,'-m','unittest','discover','-s',str(folder),'-p',pattern,'-v']
        result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
        (out/f'{name}_tests.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
        if name=='new_metadata':
            assert result.returncode==0
        else:
            assert result.returncode==1 and 'FAILED (failures=1)' in result.stderr and 'Protected code/data changed' in result.stderr
        suites.append({'name':name,'command':command,'exit_code':result.returncode,'interpretation':'All new tests pass' if name=='new_metadata' else '6 passes, 1 preserved exact-tracked-key failure; original raw bytes unchanged, known postfreeze analysis additions separately verified'})
    membership=defaultdict(list)
    for r in read_csv(out/'pool_memberships.csv'):
        membership[(r['pool_id'],r['domain'])].append(r['place_id'])
    old_membership=defaultdict(list)
    for r in read_csv(PREV/'pool_memberships.csv'):
        old_membership[r['pool_id']].append(r['place_id'])
    assert all(ids==old_membership[pool] for (pool,domain),ids in membership.items())
    assert len(membership)==21
    inputs=read_csv(out/'model_inputs.csv')
    ids_by_domain={d:[r['place_id'] for r in inputs if r['domain']==d] for d in sorted({r['domain'] for r in inputs})}
    indexes={d:{pid:i for i,pid in enumerate(ids)} for d,ids in ids_by_domain.items()}
    matrices={d:np.load(out/f'scores_{d}.npz',allow_pickle=False) for d in ids_by_domain}
    scenarios=json.loads((out/'scenarios.json').read_text(encoding='utf-8'))
    old_scenarios=json.loads((ROUND1/'scenarios.json').read_text(encoding='utf-8'))
    assert scenarios==old_scenarios
    qi={s['scenario_id']:i for i,s in enumerate(scenarios)}
    baseline=read_csv(out/'baseline_ranking_diagnostics.csv')
    for r in baseline:
        ids=membership[(r['pool_id'],r['domain'])]
        vals=matrices[r['domain']][r['method']][qi[r['scenario_id']],[indexes[r['domain']][pid] for pid in ids]]
        counts=Counter(vals.tolist());ties=sum(n*(n-1)//2 for n in counts.values())
        assert ties==int(r['exact_tied_pairs'])
        assert np.isfinite(vals).all()
        assert len(vals)==int(r['candidates'])
    robust=read_csv(out/'masking_robustness.csv')
    for r in robust:
        if r['requested_percent']=='0' or r['policy']=='B_global':
            assert float(r['topk_id_retention'])==1
            assert abs(float(r['topk_membership_probability_overlap'])-1)<1e-12
        if r['policy']=='C_described_only':
            assert int(r['rankable_count'])==int(r['remaining_described'])
        else:
            assert float(r['rankable_fraction'])==1
    assert len(baseline)==21*12*4 and len(robust)==21*12*21*5
    # All new complete-context scores must retain the previous A score exactly.
    deltas=read_csv(out/'previous_vs_new_ranking_diagnostics.csv')
    assert all(float(r['topk_id_retention'])==1 and float(r['delta_tie_fraction'])==0 for r in deltas if r['method']=='A')
    summary=read_csv(out/'annotated_method_comparison.csv')
    for r in summary:
        if r['method']=='A':assert abs(float(r['delta_vs_old_ndcg']))<1e-14
    # Independently compute tie-expected NDCG by enumerating every allowed within-band permutation.
    import itertools,math,statistics
    labels={rid:{(r['scenario_id'],r['place_id']):int(r['relevance_0_3']) for r in read_csv(out.parent/'human_evaluation_phase1_20261029_single_rater_verified'/f'validated_adapter_{rid}.csv')} for rid in ['R1','R2']}
    ranking_rows=read_csv(out/'annotated_rankings.csv');metrics=read_csv(out/'annotated_per_scenario_metrics.csv')
    checked=0
    for r in metrics:
        ranking=[x for x in ranking_rows if all(x[k]==r[k] for k in ['variant','scenario_id','method'])]
        band=defaultdict(list)
        for x in ranking:band[float(x['score'])].append(x['place_id'])
        gains={x['place_id']:2**labels[r['sheet_id']][(r['scenario_id'],x['place_id'])]-1 for x in ranking}
        k=int(r['k']);ideal=sum(g/math.log2(i+2) for i,g in enumerate(sorted(gains.values(),reverse=True)[:k]))
        if ideal==0:
            assert r['ndcg_tie_expected']=='N/A';continue
        dcg=[]
        for choice in itertools.product(*(list(itertools.permutations(band[score])) for score in sorted(band,reverse=True))):
            order=[pid for b in choice for pid in b]
            dcg.append(sum(gains[pid]/math.log2(i+2) for i,pid in enumerate(order[:k]))/ideal)
        assert abs(statistics.mean(dcg)-float(r['ndcg_tie_expected']))<1e-12
        checked+=1
    skip={'manifest.json','stage_timings.csv','runtime_measurements.csv','metadata_rerun_report.md','verification.json','output_hashes.json'}
    compared={}
    for p in sorted(out.iterdir()):
        if not p.is_file() or p.name in skip or p.name.endswith('_tests.txt'):
            continue
        other=reference/p.name
        if not other.exists():continue
        if p.suffix=='.npz':
            a,b=np.load(p,allow_pickle=False),np.load(other,allow_pickle=False)
            assert a.files==b.files and all(np.array_equal(a[k],b[k]) for k in a.files)
        else:
            assert digest(p)==digest(other),'Deterministic result differs: '+p.name
        compared[p.name]='BYTE_IDENTICAL' if digest(p)==digest(other) else 'ARRAYS_EXACTLY_EQUAL'
    verification=json.loads((out/'verification.json').read_text(encoding='utf-8'))
    assert all(digest(p)==v for p,v in verification['protected_hashes'].items())
    write_json(out/'repeat_validation.json',{'status':'PASS','reference_run':str(reference),'verified_run':str(out),'comparison_kind':'Export preservation comparison to original computed artifacts; NOT an independent second full model run','semantic_repeat':'All 4578 unique texts encoded twice in original run; exact embedding equality verified','compared_artifacts':compared,'excluded_nondeterministic_artifacts':sorted(skip),'test_suites':suites,'baseline_tie_rows_checked':len(baseline),'robustness_rows_checked':len(robust),'annotated_NDCG_rows_independently_checked_by_permutation':checked,'same_candidate_identity_lists_and_user_profiles':True,'legacy_content_verification':verify_legacy_content(),'checker_sha256':digest(__file__),'command':subprocess.list2cmdline([sys.executable,str(Path(__file__).resolve()),'--out',str(out),'--reference',str(reference)])})
    report=out/'metadata_rerun_report.md'
    text=report.read_text(encoding='utf-8').split('\n## Completed verification\n')[0]
    text+=f'\n## Completed verification\n\nAll four new metadata tests passed, including dense-versus-sparse historical score/order/tie equivalence. The original seven-test suite has six passes and one preserved failure because its exact tracked-file list predates two committed Phase 1 analysis scripts. A later analysis script also has Git CRLF worktree normalization; its LF-normalized hash matches the historical script exactly. Original frozen input/production bytes remain unchanged; preservation details are in verification.json. No earlier code or input was rewritten.\n\nExport completion preserved {len(compared)} existing numerical artifacts from the scored attempt; this is a copy-preservation comparison, not a second independent full model execution. Original semantic encoding ran twice on all 4,578 unique texts with byte-identical embeddings. New B/C annotated fit scores were independently recomputed during export and matched the saved rank scores. Independent checks recomputed all {len(baseline)} baseline tied-pair counts, checked {len(robust)} robustness rows and enumerated within-tie permutations to verify {checked} historical-label NDCG rows within 1e-12. Details: repeat_validation.json, completion_manifest.json, new_metadata_tests.txt and original_round1_tests.txt.\n'
    report.write_text(text,encoding='utf-8')
    write_json(out/'execution_notes.json',{'preserved_stopped_attempts':[{'path':'ai_metadata_20261030_run01','reason':'Prior helper rejected CRLF normalization of a postfreeze analysis script; no scoring executed'},{'path':'ai_metadata_20261030_run02','reason':'Missing platform import during manifest creation; corrected only in the new script, before model scoring'},{'path':'ai_metadata_20261030_run03','reason':'All numerical metrics saved, then a dictionary variable shadowed B model during serialization. Fixed only final export and preserved all scores.'},{'path':'ai_metadata_20261030_completed','reason':'Recovery archive writer used Windows newline translation; switched to write_bytes for exact source-code archive. Source numerical artifacts unchanged.'}],'code_changes_after_scoring':'Report source-path portability fix, final B-model export variable rename, exact-byte archive writer and independent checker addition; no score calculation or metric formula changed. Earlier outputs preserved.','no_old_experiment_or_production_changes':True})
    write_json(out/'output_hashes.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='output_hashes.json'})
    print(json.dumps({'verification':'PASS','repeat_artifacts':len(compared),'NDCG_permutation_checks':checked,'baseline_rows':len(baseline),'robustness_rows':len(robust),'output':str(out)},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--reference',type=Path,required=True)
    a=p.parse_args();verify(a.out,a.reference)
