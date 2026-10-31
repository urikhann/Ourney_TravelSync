"""Independent saved-output checks; scalar reference optimizer does not call greedy."""
import argparse,csv,gzip,hashlib,json,sys
from collections import defaultdict
from pathlib import Path
from time import perf_counter
import numpy as np
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
PREVIOUS=ROOT/'data/generated/evaluation/c_gfar_20261030_run01'

def read(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ref_rank(x,support=100):
    # Independent construction: assign a weight to each full-universe rank,
    # then average those position weights over exact tied bands.
    result=np.zeros_like(x)
    for u,row in enumerate(x):
        order=np.argsort(-row,kind='stable');values=row[order]
        starts=np.r_[0,1+np.flatnonzero(values[1:]!=values[:-1])];ends=np.r_[starts[1:],len(row)]
        weights=np.maximum(0,(support-np.arange(len(row)))/support)
        for a,b in zip(starts,ends):result[u,order[a:b]]=weights[a:b].mean()
    return result
def scalar_selection(e,ids,k,lam,z):
    total=np.zeros(len(e));left=set(range(len(ids)));picked=[]
    for t in range(min(k,len(ids))):
        candidates=[]
        for i in sorted(left,key=lambda i:ids[i]):
            values=total+e[:,i]/z/np.log2(t+2)
            score=lam*float(np.mean(values))+(1-lam)*float(np.min(values))
            candidates.append(((-round(score,12),-round(float(np.mean(values)),12),ids[i]),i,values))
        _,i,total=min(candidates,key=lambda c:c[0]);picked.append(ids[i]);left.remove(i)
    return picked
def verify(out,repeat=None):
    start=perf_counter();freeze=json.loads((out/'pre_execution_freeze.json').read_text(encoding='utf-8'))
    for p,h in freeze['protected_before'].items():assert digest(ROOT/p)==h,p
    for p,h in freeze['source_hashes'].items():assert digest(ROOT/p)==h,p
    assert digest(out/'config.json')==freeze['config_sha256']
    groups=json.loads((out/'group_profiles.json').read_text(encoding='utf-8'))
    assert groups==json.loads((PREVIOUS/'group_profiles.json').read_text(encoding='utf-8'))
    assert json.loads((out/'description_masks.json').read_text(encoding='utf-8'))==json.loads((PREVIOUS/'description_masks.json').read_text(encoding='utf-8'))
    rows=read(out/'per_group_results.csv');by=defaultdict(list)
    for r in rows:by[r['group_id']].append(r)
    pools={r['group_id']:r['poi_ids'] for r in json.loads((out/'candidate_unions.json').read_text(encoding='utf-8'))}
    full=np.load(PREVIOUS/'full_profile_score_matrices.npz',allow_pickle=False);all_ids=[r['place_id'] for r in read(PREVIOUS/'poi_index.csv')];pos={p:i for i,p in enumerate(all_ids)}
    meta=ROOT/'data/generated/evaluation/ai_metadata_20261030_verified'
    assert all_ids==[r['place_id'] for r in read(meta/'model_inputs.csv') if r['domain']=='enriched_catalogue']
    assert len(all_ids)==len(set(all_ids))==3568
    original=np.load(meta/'scores_enriched_catalogue.npz',allow_pickle=False)
    for key in full.files:np.testing.assert_array_equal(full[key],original[key])
    canonical={r['place_id']:r for r in read(ROOT/'src/ourney_travelsync/data/travelsync_final_places_v2.csv')}
    manifest=json.loads((meta/'manifest.json').read_text(encoding='utf-8'))
    canonical_path=ROOT/'src/ourney_travelsync/data/travelsync_final_places_v2.csv'
    assert digest(canonical_path)==manifest['input_hashes'][str(canonical_path)]
    metric_checks=0;replay_checks=0
    evaluation_start=perf_counter()
    for g in groups:
        gid=g['group_id'];b=np.load(PREVIOUS/'group_scores'/f'{gid}.npz',allow_pickle=False);ids=b['poi_ids'].tolist();lookup={p:i for i,p in enumerate(ids)};ps=b['profile_indices'];ix=[pos[p] for p in ids]
        assert ps.tolist()==[m['profile_index'] for m in g['members']]
        assert b['member_ids'].tolist()==[m['member_id'] for m in g['members']]
        for f in g['excluded_features']:assert all(float(canonical[p][f])==0 for p in ids)
        evaluators={m+'_rank':ref_rank(full[m][np.ix_(ps,ix)]) for m in 'AC'}
        ideals={(a,k):np.sort(e,axis=1)[:,::-1][:,:k]@(1/np.log2(np.arange(2,min(k,len(ids))+2))) for a,e in evaluators.items() for k in [5,10,20]}
        # Independent metric recomputation for EVERY returned list, masks included.
        for r in by[gid]:
            picked=r['topk_ids'].split('|') if r['topk_ids'] else [];cols=[lookup[p] for p in picked];k=int(r['K']);assert len(set(picked))==len(picked)==int(r['returned'])
            if r['stage']!='sensitivity':assert set(picked)<=set(pools[gid])
            if 'Max-Min' in r['method'] or 'Hybrid' in r['method']:assert len(picked)==min(k,int(r['candidate_count']))
            if picked:
                values=(evaluators[r['assessor']][:,cols]@(1/np.log2(np.arange(2,len(cols)+2))))/ideals[r['assessor'],k]
                np.testing.assert_allclose(values,json.loads(r['member_utilities_json']),rtol=0,atol=1e-12)
                assert abs(float(values.min())-float(r['minimum_utility']))<1e-12
                assert abs(float(values.mean())-float(r['mean_utility']))<1e-12
                assert abs(float(np.ptp(values))-float(r['utility_gap']))<1e-12
            else:assert not r['minimum_utility']
            metric_checks+=1
        # Independent scalar implementation checks all 105 primary new-method rankings.
        cp=[lookup[p] for p in pools[gid]];e=ref_rank(b['C']);z=np.sort(e,axis=1)[:,::-1][:,:10]@(1/np.log2(np.arange(2,12)))
        primary=[r for r in by[gid] if r['stage']=='primary' and r['assessor']=='C_rank'];pm={r['method']:r for r in primary}
        for method in ['C+Max-Min','C+Hybrid(0.00)','C+Hybrid(0.25)','C+Hybrid(0.50)','C+Hybrid(0.75)','C+Hybrid(1.00)']:
            lam=0 if 'Max-Min' in method else float(method.split('(')[1][:-1]);actual=scalar_selection(e[:,cp],pools[gid],10,lam,z)
            assert '|'.join(actual)==pm[method]['topk_ids'],(gid,method);replay_checks+=1
        assert pm['C+Max-Min']['topk_ids']==pm['C+Hybrid(0.00)']['topk_ids']
    elapsed_evaluation=perf_counter()-evaluation_start
    # Numerical baseline checks against the values explicitly supplied by user.
    summary=read(out/'primary_summary.csv')
    expected={'C+AVG':(.5152471898807898,.6508151137398925),'C+AWM':(.4308899697545489,.5140051869761961),'C+GFAR':(.47556506988089453,.6400268805722927)}
    for m,(a,b) in expected.items():
        r=next(r for r in summary if r['assessor']=='C_rank' and r['method']==m)
        assert abs(float(r['mean_minimum'])-a)<1e-12;assert abs(float(r['mean_member'])-b)<1e-12
    # Historical baseline ranking/NDCG regression against the unchanged prior blocks.
    old_lists={(r['group_id'],r['scorer']+'+'+r['aggregator']):r for r in read(PREVIOUS/'human_group_lists.csv')}
    human_regressions=0
    for r in read(out/'human_group_lists.csv'):
        if (r['group_id'],r['method']) in old_lists:
            old=old_lists[r['group_id'],r['method']]
            assert r['candidate_ids']==old['candidate_ids'] and r['topk_ids']==old['topk_ids']
            human_regressions+=1
    old_human={(r['group_id'],r['scorer']+'+'+r['aggregator'],r['sheet_id'],r['member_id']):r for r in read(PREVIOUS/'human_per_member_metrics.csv')}
    for r in read(out/'human_per_member_metrics.csv'):
        key=(r['group_id'],r['method'],r['sheet'],r['member_id'])
        if key in old_human:assert r['human_ndcg']==old_human[key]['human_ndcg']
    trace_count=0
    with gzip.open(out/'selection_traces.jsonl.gz','rt',encoding='utf-8') as f:
        for line in f:
            t=json.loads(line);trace_count+=1
            if 'member_utilities_after' in t:
                values=t['member_utilities_after'];assert abs(min(values)-t['minimum_utility'])<1e-12
                assert t['objective_tie_count']>=t['final_tie_count']>=1
    repeat_checks=[]
    if repeat:
        # Timing and run manifests intentionally excluded; complete scientific tables compared.
        for p in sorted(out.glob('*.csv')):
            if p.name.startswith('runtime'):continue
            q=repeat/p.name;assert q.exists(),p.name
            assert digest(p)==digest(q),p.name;repeat_checks.append(p.name)
        for name in ['candidate_unions.json','group_profiles.json','description_masks.json']:
            assert digest(out/name)==digest(repeat/name);repeat_checks.append(name)
        for name in ['selection_traces.jsonl.gz','human_selection_traces.jsonl.gz']:
            with gzip.open(out/name,'rb') as a,gzip.open(repeat/name,'rb') as b:
                while True:
                    aa=a.read(1024*1024);bb=b.read(1024*1024);assert aa==bb,name
                    if not aa:break
            repeat_checks.append(name+' (decompressed)')
    result={'status':'PASS','protected_hashes_checked':len(freeze['protected_before']),'metric_rows_independently_recomputed':metric_checks,'primary_rankings_scalar_replayed':replay_checks,'selection_trace_rows_checked':trace_count,'human_baseline_lists_reproduced':human_regressions,'full_repeat_scientific_files_identical':repeat_checks,'independent_evaluation_and_scalar_replay_seconds':elapsed_evaluation,'total_verification_seconds':perf_counter()-start,'baseline_means_reproduced':True,'groups_masks_sources_and_labels_preserved':True}
    (out/'final_validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--repeat',type=Path);a=p.parse_args();verify(a.out,a.repeat)
