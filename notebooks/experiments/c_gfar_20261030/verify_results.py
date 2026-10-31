"""Artifact invariants, independent GFAR replay, historical NDCG and test logs."""
import argparse,csv,gzip,json,math,subprocess,sys
from collections import defaultdict
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE))
from experiment import ROOT,META,PHASE1,ROUND1,digest,read_csv,write_json,calibrate,mask_scores
from aggregation import policy_backbone,expected_rank_relevance,utilities,utility_metrics


def main(out):
    hashes=json.loads((out/'output_hashes.json').read_text(encoding='utf-8'))
    assert all(digest(out/p)==value for p,value in hashes.items())
    frozen=json.loads((out/'pre_execution_freeze.json').read_text(encoding='utf-8'))
    assert all(digest(p)==v for p,v in frozen['code_hashes'].items())
    checks=json.loads((out/'verification.json').read_text(encoding='utf-8'))
    assert all(digest(p)==v for p,v in checks['protected_hashes'].items())
    suites=[]
    commands=[('experiment_tests',[sys.executable,'-m','unittest','discover','-s',str(HERE),'-p','test_*.py','-v']),
              ('production_tests',[str(ROOT/'.venv/Scripts/python.exe'),'-c',"import sys; sys.path.insert(0,'src'); import pytest; raise SystemExit(pytest.main(['--noconftest','tests/test_algorithms.py','tests/test_recommender.py','-q','-p','no:cacheprovider','--basetemp','.uv-cache/pytest-c-gfar-production']))"]),
              ('SDD_existing_fixture_tests',[str(ROOT/'.venv/Scripts/python.exe'),'-c',"import sys; sys.path.insert(0,'services/mocks'); import pytest; raise SystemExit(pytest.main(['--noconftest','api/tests/test_group_rec.py','-q','-p','no:cacheprovider','--basetemp','.uv-cache/pytest-c-gfar-sdd']))"])]
    for name,cmd in commands:
        r=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True)
        (out/(name+'.txt')).write_text(r.stdout+r.stderr,encoding='utf-8')
        suites.append({'suite':name,'command':cmd,'exit_code':r.returncode,'log':name+'.txt'})
        assert r.returncode==0,name+' failed; see preserved log'
    rows=read_csv(out/'per_group_results.csv');runs={r['run_id']:r for r in rows if r['assessor']=='C_rank'}
    assert len(rows)==33600 and len(runs)==16800
    groups={r['group_id']:r for r in json.loads((out/'group_profiles.json').read_text(encoding='utf-8'))}
    candidates={}
    with gzip.open(out/'candidate_unions.jsonl.gz','rt',encoding='utf-8') as f:
        for line in f:
            r=json.loads(line);candidates[(r['group_id'],r['condition'])]=r['poi_ids']
    for r in rows:
        chosen=r['topk_ids'].split('|') if r['topk_ids'] else []
        assert len(chosen)==int(r['returned']) and len(set(chosen))==len(chosen)<=10
        poolkey=r['scorer']+'_specific' if r['stage']=='candidate_diagnostic' else 'shared'
        assert set(chosen)<=set(candidates[(r['group_id'],poolkey)])
        values=json.loads(r['member_utilities_json'])
        m=utility_metrics(values)
        for key in ['minimum_utility','mean_utility','utility_gap','utility_std','jain']:
            if m[key] is None:assert r[key]==''
            else:assert abs(m[key]-float(r[key]))<1e-12
        if not chosen:assert all(v is None for v in values)
    primary={(r['group_id'],r['scorer'],r['aggregator'],r['assessor']):r for r in rows if r['stage']=='primary'}
    for r in rows:
        if r['stage']=='masking' and r['mask_percent']=='0' and r['fallback_policy']=='C_native':
            assert r['topk_ids']==primary[(r['group_id'],'C',r['aggregator'],r['assessor'])]['topk_ids']
    # Independent Python scalar greedy implementation for sampled complete traces.
    scores=np.load(out/'full_profile_score_matrices.npz',allow_pickle=False)
    poi=[r['place_id'] for r in read_csv(out/'poi_index.csv')];globalpos={p:i for i,p in enumerate(poi)}
    masks={m['mask_id']:set(m['masked_place_ids']) for m in json.loads((out/'description_masks.json').read_text())}
    trace_count=0;replayed=0;alltrace=defaultdict(list)
    with gzip.open(out/'selection_traces.jsonl.gz','rt',encoding='utf-8') as f:
        for line in f:
            t=json.loads(line);trace_count+=1
            p,q,after=map(np.asarray,[t['member_relevance'],t['unmet_before'],t['unmet_after']])
            assert abs(float(p@q)-t['selection_gain'])<1e-12
            np.testing.assert_allclose(after,q*(1-p),rtol=0,atol=1e-12)
            assert np.all(after<=q+1e-15)
            if t['run_id'] in runs and (runs[t['run_id']]['stage']=='primary' or int(t['run_id'][1:])%83==0):alltrace[t['run_id']].append(t)
    def reconstruct(r):
        g=groups[r['group_id']];bundle=np.load(out/'group_scores'/(g['group_id']+'.npz'),allow_pickle=False);ids=bundle['poi_ids'].tolist();ps=bundle['profile_indices'];ix=[globalpos[p] for p in ids]
        raw=bundle[r['scorer']]
        if r['stage']=='masking':
            lost=bundle['initial_targeted_description_loss']|np.asarray([p in masks[r['mask_id']] for p in ids])
            raw=mask_scores(scores['C'][np.ix_(ps,ix)],scores['C_metadata_projection'][np.ix_(ps,ix)],lost)
            if r['fallback_policy']=='B_backbone_C_ties':raw=np.vstack([policy_backbone(b,c) for b,c in zip(bundle['B'],raw)])
        return g,bundle,ids,raw,ps,ix
    for rid,trace in alltrace.items():
        r=runs[rid];g,bundle,ids,raw,ps,ix=reconstruct(r);users=bundle['member_ids'].tolist()
        poolkey=r['scorer']+'_specific' if r['stage']=='candidate_diagnostic' else 'shared';pool=candidates[(g['group_id'],poolkey)];idx={p:i for i,p in enumerate(ids)}
        cal=calibrate(raw,r['calibration']);n=min(int(r['gfar_depth']),len(pool));rel={u:{} for u in users}
        # Preserve the adapter's native floating mean contract. A scalar sum or
        # F-contiguous slice can differ by one ulp at an exact secondary tie.
        # Gains/unmet states below remain independently computed in scalar code.
        native_means=np.ascontiguousarray(cal[:,[idx[p] for p in pool]]).mean(axis=0)
        mean_by_id=dict(zip(pool,native_means.tolist()))
        for u,user in enumerate(users):
            ranked=sorted(pool,key=lambda p:(-raw[u,idx[p]],p))[:n]
            den=n*(n-1)/2
            rel[user]={p:(n-rank-1)/den if den else 1. for rank,p in enumerate(ranked)}
        q={u:1. for u in users};remaining=set(pool)
        for t in trace:
            gains={p:sum(rel[u].get(p,0)*q[u] for u in sorted(users)) for p in remaining}
            means={p:mean_by_id[p] for p in remaining}
            pick=min(remaining,key=lambda p:(-round(gains[p],12),-means[p],p))
            assert pick==t['selected_poi_id'],f'Independent replay differs: {rid}'
            assert abs(gains[pick]-t['selection_gain'])<1e-12
            for u in users:q[u]*=1-rel[u].get(pick,0)
            remaining.remove(pick)
        assert [t['selected_poi_id'] for t in trace]==r['topk_ids'].split('|')
        replayed+=1
    # Recompute utility from the fixed native assessor, not saved metric columns.
    utility_checks=0
    for r in rows:
        if r['stage']!='primary' and int(r['run_id'][1:])%113!=0:continue
        g,bundle,ids,raw,ps,ix=reconstruct(r);lookup={p:i for i,p in enumerate(ids)}
        selected=[lookup[p] for p in r['topk_ids'].split('|')] if r['topk_ids'] else []
        native=scores[r['assessor'][0]][np.ix_(ps,ix)]
        actual=utilities(expected_rank_relevance(native,100),selected,10);saved=json.loads(r['member_utilities_json'])
        assert all((a is None and b is None) or (a is not None and b is not None and abs(a-b)<1e-12) for a,b in zip(actual,saved))
        utility_checks+=1
    # Explicit hard bans hold for full candidate pools and all chosen lists.
    canonical={r['place_id']:r for r in read_csv(META/'inputs/travelsync_poi_metadata_enriched_v1.csv')}
    for g in groups.values():
        if g['excluded_features']:
            for (gid,condition),pids in candidates.items():
                if gid==g['group_id']:assert all(float(canonical[p]['food'])==0 for p in pids)
    humanlists={(r['group_id'],r['scorer'],r['aggregator']):r for r in read_csv(out/'human_group_lists.csv')}
    grades={rid:{(r['scenario_id'],r['place_id']):int(r['relevance_0_3']) for r in read_csv(PHASE1/f'validated_adapter_{rid}.csv')} for rid in ['R1','R2']}
    human_checks=0
    for r in read_csv(out/'human_per_member_metrics.csv'):
        listing=humanlists[(r['group_id'],r['scorer'],r['aggregator'])];pool=listing['candidate_ids'].split('|');chosen=listing['topk_ids'].split('|') if listing['topk_ids'] else [];k=int(r['K'])
        gains={p:2**grades[r['sheet_id']][(r['scenario_id'],p)]-1 for p in pool};ideal=sum(g/math.log2(j+2) for j,g in enumerate(sorted(gains.values(),reverse=True)[:k]))
        if not chosen or not ideal:assert r['human_ndcg']==''
        else:assert abs(sum(gains[p]/math.log2(j+2) for j,p in enumerate(chosen))/ideal-float(r['human_ndcg']))<1e-12
        human_checks+=1
    assert all(digest(p)==value for p,value in checks['protected_hashes'].items())
    write_json(out/'final_validation.json',{'status':'PASS','test_suites':suites,'group_assessor_rows_checked':len(rows),'trace_state_updates_checked':trace_count,'independent_scalar_GFAR_replays':replayed,'native_evaluator_utility_rows_recomputed':utility_checks,'historical_human_NDCG_rows_recomputed':human_checks,'hard_exclusions_hold':True,'zero_mask_native_matches_primary':True,'protected_sources_and_documents_unchanged':True,'independent_replay_precision_note':'Scalar gain/state replay initially differed at R004897 step9 only because a scalar or F-contiguous proxy-mean sum differs by one ulp from the declared C-contiguous native NumPy mean tie-break. Verifier now uses that exact mean reduction contract; all GFAR gain/state updates remain scalar and independent. No recommendations, calibration parameters or model scores changed.','initial_environment_failure':'Semantic environment lacks pytest. Preserved initial missing-pytest log; existing pytest suites then executed in the project environment, without installing anything into the application environment.','source_discrepancy':'Initial paper-example expected gain used extracted Table2 2/27, failed against Eq5. Corrected fixture assertion to the directly derived 2/9, preserving selection recurrence; no experiment parameters changed.','code_added_after_scoring_freeze':{p.name:digest(p) for p in HERE.glob('*.py')},'command':subprocess.list2cmdline([sys.executable,str(Path(__file__).resolve()),'--out',str(out)])})
    report=out/'REPORT.md';text=report.read_text(encoding='utf-8')
    text+='\n## Completed validation\n\nAll 28 experiment/regression tests passed. The existing production tests and six SDD fixture tests were executed separately with API conftest disabled to avoid creating/deleting its SQLite test database. Exact logs and pass counts are preserved. Cached D regression verifies its frozen matrix hash and recorded repeat embedding check; the encoder was not rerun in this task.\n\n'+f'All {len(rows)} group/assessor rows passed list and utility-summary invariants; {trace_count} trace state transitions were checked, {replayed} complete GFAR selections were independently replayed with scalar arithmetic, {utility_checks} native-assessor utility rows were recomputed, and all {human_checks} historical human NDCG rows were recalculated. Shared/own candidate membership, explicit exclusions and zero-mask identity were checked. No prior data, model, SRS/SDD or production content changed. See final_validation.json.\n'
    report.write_text(text,encoding='utf-8')
    write_json(out/'output_hashes.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='output_hashes.json'})
    print(json.dumps({'status':'PASS','rows':len(rows),'traces':trace_count,'replayed_runs':replayed,'utility_rows':utility_checks,'human_NDCG':human_checks},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();main(a.out.resolve())
