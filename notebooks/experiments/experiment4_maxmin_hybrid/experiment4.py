"""Experiment 4. All writes confined to a fresh output directory (unique module)."""
import argparse,csv,gzip,hashlib,importlib.metadata,itertools,json,platform,statistics,subprocess,sys
from collections import defaultdict
from pathlib import Path
from time import perf_counter
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PREVIOUS=ROOT/'data/generated/evaluation/c_gfar_20261030_run01'
sys.path.insert(0,str(HERE.parent/'c_gfar_20261030'))
from aggregation import aggregate,calibrate,expected_rank_relevance,utilities,utility_metrics,policy_backbone
from experiment import META,ROUND1,PHASE1,FEATURES,SparseLexical,sparse_scores,clean,score_individuals
from optimizer import greedy,exact,ideal

LAMBDAS=[0.,.25,.5,.75,1.]
METHODS=['C+AVG','C+AWM','C+GFAR','C+Max-Min']+[f'C+Hybrid({l:.2f})' for l in LAMBDAS]+['A+AVG','A+GFAR']
MASK_METHODS=[m for m in METHODS if m.startswith('C+') and m!='C+AWM']

def read(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def js(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def table(p,rows):
    rows=list(rows)
    if not rows:return
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def mean(a):
    a=[x for x in a if x is not None];return statistics.mean(a) if a else None
def snapshot():
    paths=[]
    for folder in [ROOT/'src',ROOT/'api',ROOT/'services',ROOT/'docs',ROOT/'contracts',ROOT/'data',HERE.parent]:
        for p in folder.rglob('*'):
            if p.is_file() and not any(s in p.parts for s in ['__pycache__','.semantic-venv','.plot-venv','.plot-cache','.venv','.pytest_cache']) and HERE not in p.parents:
                paths.append(p)
    return {str(p.relative_to(ROOT)):digest(p) for p in sorted(set(paths))}

def run(out):
    if out.exists():raise FileExistsError('Choose a fresh output directory')
    before=snapshot() # taken before creating new result files
    out.mkdir(parents=True)
    config={'version':1,'primary_assessor':'C_rank','secondary_assessor':'A_rank','primary_optimization':'expected triangular top100 C-rank relevance from each group frozen recommendation scores; full hard-eligible fixed-K ideal','K':10,'L':100,'gfar_depth':20,'lambdas':LAMBDAS,'methods':METHODS,'seeds':[11,29,47],'mask_seeds':[11,29,47,71,101],'mask_percentages':[0,10,25,50,75],'tie_policy':'New optimizer: objective and secondary mean rounded12, then stable ID; members sorted. Legacy baselines unchanged.','sensitivity':{'L':[50,200],'K':[5,20],'optimization_inputs':['percentile','minmax']},'human':'No fitting or lambda selection on labels. One annotator, two historical sheets; six fully judged blocks, three synthetic group compositions each.','candidate_policy':'Exact Experiment 3 saved shared A/C top100 union, fixed across masks. Separate L sensitivity reconstructs A/C union from same saved scores.','discount':'1/log2(t+1)','normalization':'Ideal topK over full hard-eligible universe, fixed across prefix lengths; undefined group if any ideal=0','decision_frozen_before_results':True}
    js(out/'config.json',config)
    groups=json.loads((PREVIOUS/'group_profiles.json').read_text(encoding='utf-8'))
    masks=json.loads((PREVIOUS/'description_masks.json').read_text(encoding='utf-8'))
    profiles=json.loads((PREVIOUS/'individual_profiles.json').read_text(encoding='utf-8'))
    full=np.load(PREVIOUS/'full_profile_score_matrices.npz',allow_pickle=False)
    all_ids=[r['place_id'] for r in read(PREVIOUS/'poi_index.csv')];global_pos={p:i for i,p in enumerate(all_ids)}
    pools={}
    with gzip.open(PREVIOUS/'candidate_unions.jsonl.gz','rt',encoding='utf-8') as f:
        for line in f:
            r=json.loads(line)
            if r['condition']=='shared':pools[r['group_id']]=r['poi_ids']
    source_refs=[PREVIOUS/'group_profiles.json',PREVIOUS/'individual_profiles.json',PREVIOUS/'description_masks.json',PREVIOUS/'full_profile_score_matrices.npz',PREVIOUS/'candidate_unions.jsonl.gz',PREVIOUS/'poi_index.csv']
    freeze={'config_sha256':digest(out/'config.json'),'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=ROOT).strip(),'git_status':subprocess.check_output(['git','status','--short'],text=True,cwd=ROOT),'source_hashes':{str(p.relative_to(ROOT)):digest(p) for p in source_refs},'code_hashes':{str(p.relative_to(ROOT)):digest(p) for p in list(HERE.glob('*.py'))+[HERE.parent/'c_gfar_20261030/aggregation.py']},'python':sys.version,'platform':platform.platform(),'dependencies':{n:importlib.metadata.version(n) for n in ['numpy','scipy']},'protected_before':before}
    js(out/'pre_execution_freeze.json',freeze)
    js(out/'group_profiles.json',groups);js(out/'description_masks.json',masks)
    results=[];members=[];times=[];runs=0;regress=[];candidate_records=[]
    traces=gzip.open(out/'selection_traces.jsonl.gz','wt',encoding='utf-8')
    old_primary={(r['group_id'],r['scorer']+'+'+r['aggregator'],r['assessor']):r for r in read(PREVIOUS/'per_group_results.csv') if r['stage']=='primary'}
    inputs=[r for r in read(META/'model_inputs.csv') if r['domain']=='enriched_catalogue']
    spec=json.loads((META/'lexical_models.json').read_text(encoding='utf-8'))['enriched_catalogue']['C']
    model=object.__new__(SparseLexical);model.vocab=spec['vocabulary'];model.index={v:i for i,v in enumerate(model.vocab)};model.idf=np.asarray(spec['idf'])
    start=perf_counter();docs=model.encode([r['C_D_text'] for r in inputs]);preprocess=perf_counter()-start
    canonical={r['place_id']:r for r in read(ROOT/'src/ourney_travelsync/data/travelsync_final_places_v2.csv')}
    feature=np.asarray([[float(canonical[p][f]) for f in FEATURES] for p in all_ids])
    queries=[clean(p['preference_ko']) for p in profiles]
    prefs=np.asarray([[p['preference_vector'][f] for f in FEATURES] for p in profiles])
    cache={}
    for gi,g in enumerate(groups):
        bundle=np.load(PREVIOUS/'group_scores'/f'{g["group_id"]}.npz',allow_pickle=False)
        ids=bundle['poi_ids'].tolist();users=bundle['member_ids'].tolist();ps=bundle['profile_indices'];gx=[global_pos[p] for p in ids];lookup={p:i for i,p in enumerate(ids)}
        raw={m:bundle[m] for m in 'ABC'};pool=[lookup[p] for p in pools[g['group_id']]]
        candidate_records.append({'group_id':g['group_id'],'poi_ids':pools[g['group_id']],'sha256':hashlib.sha256('\n'.join(pools[g['group_id']]).encode()).hexdigest()})
        evaluators={m+'_rank':expected_rank_relevance(full[m][np.ix_(ps,gx)],100) for m in 'AC'}
        # Warm, restored-index scoring measured anew. Not application latency.
        scoring={}
        for scorer in 'AC':
            start=perf_counter()
            if scorer=='C':recomputed=sparse_scores(model.encode([queries[p] for p in ps]),docs[gx])
            else:recomputed=score_individuals(prefs[ps],feature[gx])
            scoring[scorer]=(perf_counter()-start)*1000
            np.testing.assert_allclose(recomputed,full[scorer][np.ix_(ps,gx)],rtol=0,atol=1e-12)
        start=perf_counter();check=set()
        for scorer in 'AC':
            for row in raw[scorer]:check.update(sorted(range(len(ids)),key=lambda i:(-row[i],ids[i]))[:100])
        generation=(perf_counter()-start)*1000
        assert sorted(check,key=lambda i:ids[i])==pool
        cache={}
        def execute(method,score,stage='primary',k=10,chosen_pool=None,mask_id='',percent=0,policy='native',opt='rank',sensitivity=''):
            nonlocal runs
            runs+=1;rid=f'E4_{runs:06d}';cp=pool if chosen_pool is None else chosen_pool;ci=[ids[i] for i in cp]
            scorer=method[0];alg=method.split('+',1)[1]
            key=(id(score),opt,k)
            if key not in cache:
                start=perf_counter();scaled=calibrate(score)
                relevance=expected_rank_relevance(score,100) if opt=='rank' else calibrate(score,opt)
                z=ideal(relevance,k);cache[key]=(scaled,relevance,z,(perf_counter()-start)*1000)
            scaled,relevance,z,normalization=cache[key]
            start=perf_counter()
            if alg in ['AVG','AWM','GFAR']:
                picked,trace,info=aggregate(score[:,cp],scaled[:,cp],ci,users,alg,k,20,.5)
                info['status']='OK' if len(picked)==k else 'AWM_FILTER_SHORTAGE' if alg=='AWM' else 'CANDIDATE_SHORTAGE'
            else:
                lam=0. if alg=='Max-Min' else float(alg[7:-1])
                picked,trace,info=greedy(relevance[:,cp],ci,users,k,z,lam)
            elapsed=(perf_counter()-start)*1000
            cols=[lookup[p] for p in picked]
            for t in trace:traces.write(json.dumps({'run_id':rid,'group_id':g['group_id'],'method':method,'stage':stage,**t})+'\n')
            opt_values=utilities(relevance,cols,k)
            counts=[np.unique(row[cp],return_counts=True)[1] for row in score]
            pair_count=len(cp)*(len(cp)-1)/2
            tie_fraction=mean([float(np.sum(c*(c-1)/2)/pair_count) if pair_count else 0. for c in counts])
            for assessor,e in evaluators.items():
                values=utilities(e,cols,k);metrics=utility_metrics(values)
                represented=[bool(cols and (row[cols]>0).any()) for row in e]
                r={'run_id':rid,'group_id':g['group_id'],'type':g['type'],'size':g['size'],'seed':g['seed'],'stage':stage,'method':method,'assessor':assessor,'K':k,'L':100 if not sensitivity.startswith('L=') else int(sensitivity[2:]),'optimization_input':opt,'sensitivity':sensitivity,'mask_id':mask_id,'mask_percent':percent,'policy':policy,'candidate_count':len(cp),'eligible_count':len(ids),'returned':len(picked),'status':info['status'],'topk_ids':'|'.join(picked),'member_utilities_json':json.dumps(values),'member_support_coverage':mean(represented) if picked else None,'exact_tie_fraction':tie_fraction,'optimization_minimum':min(opt_values) if opt_values and all(v is not None for v in opt_values) else None,'optimization_mean':mean(opt_values),**metrics}
                results.append(r)
                for u,v,rep in zip(users,values,represented):members.append({'run_id':rid,'group_id':g['group_id'],'assessor':assessor,'member_id':u,'rank_utility':v,'represented':rep if picked else None})
                if stage=='primary' and (g['group_id'],method,assessor) in old_primary:
                    old=old_primary[g['group_id'],method,assessor]
                    assert r['topk_ids']==old['topk_ids']
                    for field in metrics:
                        if metrics[field] is not None:assert abs(metrics[field]-float(old[field]))<1e-12
                    regress.append({'group_id':g['group_id'],'method':method,'assessor':assessor,'ranking_identical':True,'metrics_identical_tolerance_1e12':True})
            times.append({'run_id':rid,'group_id':g['group_id'],'stage':stage,'method':method,'size':g['size'],'mask_percent':percent,'policy':policy,'aggregation_ms':elapsed,'normalization_ms':normalization,'warm_scoring_ms':scoring[scorer],'candidate_generation_ms':generation,'warm_offline_sum_ms':elapsed+normalization+scoring[scorer]+generation,'timing_note':'Summed measured stages, warm restored lexical index; excludes fitting, document encoding, evaluation, output, application and network. Mask conditions use cached projections, not rescored narratives.'})
        for method in METHODS:execute(method,raw[method[0]])
        for l in [50,200]:
            cp=set()
            for scorer in 'AC':
                for row in raw[scorer]:cp.update(sorted(range(len(ids)),key=lambda i:(-row[i],ids[i]))[:l])
            cp=sorted(cp,key=lambda i:ids[i])
            for method in MASK_METHODS:execute(method,raw['C'],'sensitivity',chosen_pool=cp,sensitivity=f'L={l}')
        for k in [5,20]:
            for method in MASK_METHODS:execute(method,raw['C'],'sensitivity',k=k,sensitivity=f'K={k}')
        for opt in ['percentile','minmax']:
            for method in MASK_METHODS:
                if 'Hybrid' in method or 'Max-Min' in method:execute(method,raw['C'],'sensitivity',opt=opt,sensitivity=opt)
        for mask in masks:
            lost=bundle['initial_targeted_description_loss']|np.isin(ids,mask['masked_place_ids'])
            c=np.where(lost[None,:],full['C_metadata_projection'][np.ix_(ps,gx)],full['C'][np.ix_(ps,gx)])
            backbone=np.vstack([policy_backbone(b,row) for b,row in zip(raw['B'],c)])
            for policy,scores in [('C_native',c),('B_backbone_C_ties',backbone)]:
                # Keep references alive during cache lifetime; reset for temporary matrices.
                cache={}
                for method in MASK_METHODS:execute(method,scores,'masking',mask_id=mask['mask_id'],percent=mask['requested_percent'],policy=policy)
        if gi%10==0:print(f'Completed groups {gi+1}/105; runs {runs}',flush=True)
    traces.close()
    table(out/'per_group_results.csv',results);table(out/'per_member_results.csv',members);table(out/'runtime_measurements.csv',times);table(out/'baseline_regression.csv',regress)
    js(out/'candidate_unions.json',candidate_records)
    human(out,full,all_ids)
    controls(out)
    # Protection check includes previous experiment code, all previous outputs and data.
    changed=[p for p,h in before.items() if not (ROOT/p).is_file() or digest(ROOT/p)!=h]
    assert not changed,changed
    js(out/'verification.json',{'protected_files':len(before),'protected_changed':changed,'groups':len(groups),'runs':runs,'assessor_rows':len(results),'baseline_regression_rows':len(regress),'all_candidate_unions_identical':True,'preprocessing_document_encoding_seconds':preprocess,'source_dataset_rows':len(canonical),'eligible_universe':len(all_ids)})
    from experiment4_report import generate
    generate(out,results,times)
    print(json.dumps({'out':str(out),'runs':runs,'rows':len(results),'regression_rows':len(regress)}),flush=True)

def controls(out):
    rows=[]
    for seed in [11,29,47,71,101]:
        x=np.random.default_rng(seed).integers(0,5,(3,7))/4;ids=list('abcdefg');users=['u1','u2','u3'];z=ideal(x,3)
        for lam in LAMBDAS:
            picked,t,info=greedy(x,ids,users,3,z,lam);optimal,value=exact(x,ids,users,3,z,lam)
            rows.append({'seed':seed,'lambda':lam,'greedy_ids':'|'.join(picked),'exact_ids':'|'.join(optimal),'greedy_objective':info['objective'],'exact_objective':value,'absolute_gap':max(0.,value-info['objective'])})
    table(out/'exact_controls.csv',rows)

def human(out,full,all_ids):
    pos={p:i for i,p in enumerate(all_ids)};templates=read(ROUND1/'results/annotation_R1.csv')
    grades={sheet:{(r['scenario_id'],r['place_id']):int(r['relevance_0_3']) for r in read(PHASE1/f'validated_adapter_{sheet}.csv')} for sheet in ['R1','R2']}
    rows=[];lists=[]
    with gzip.open(out/'human_selection_traces.jsonl.gz','wt',encoding='utf-8') as tracefile:
        for block in range(6):
            ids=sorted({r['place_id'] for r in templates if r['scenario_id']==f'S{2*block+1:02d}'});ix=[pos[p] for p in ids];k=min(3,len(ids))
            for kind,ps in [('mixed2',[2*block,2*block+1]),('minority3',[2*block,2*block,2*block+1]),('polarized4',[2*block,2*block,2*block+1,2*block+1])]:
                users=[f'u{i:02d}' for i in range(len(ps))];gid=f'human_block{block}_{kind}'
                for method in METHODS:
                    scorer=method[0];alg=method.split('+',1)[1];x=full[scorer][np.ix_(ps,ix)]
                    if alg in ['AVG','AWM','GFAR']:picked,trace,_=aggregate(x,calibrate(x),ids,users,alg,k,20,.5)
                    else:
                        # Optimize rank proxy over restricted historical block, never labels.
                        e=expected_rank_relevance(x,100);lam=0 if alg=='Max-Min' else float(alg[7:-1]);picked,trace,_=greedy(e,ids,users,k,ideal(e,k),lam)
                    lists.append({'group_id':gid,'method':method,'K':k,'returned':len(picked),'candidate_ids':'|'.join(ids),'topk_ids':'|'.join(picked)})
                    for t in trace:tracefile.write(json.dumps({'group_id':gid,'method':method,**t})+'\n')
                    for sheet,label in grades.items():
                        for u,p in enumerate(ps):
                            sid=f'S{p+1:02d}';gains={pid:2**label[sid,pid]-1 for pid in ids};d=1/np.log2(np.arange(2,k+2));z=np.asarray(sorted(gains.values(),reverse=True)[:k])@d
                            v=float(np.asarray([gains[p] for p in picked])@d[:len(picked)]/z) if picked and z else None
                            rows.append({'group_id':gid,'block':block,'type':kind,'method':method,'sheet':sheet,'member_id':users[u],'scenario_id':sid,'K':k,'returned':len(picked),'human_ndcg':v,'zero_ideal':bool(z==0)})
    table(out/'human_per_member_metrics.csv',rows);table(out/'human_group_lists.csv',lists)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);run(p.parse_args().out.resolve())
