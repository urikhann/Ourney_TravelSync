"""Frozen-score Method C x group aggregation factorial experiment."""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import argparse,csv,gzip,hashlib,importlib.metadata,json,math
from pathlib import Path
import platform,random,statistics,subprocess,sys
from time import perf_counter
import numpy as np

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
META=ROOT/'data/generated/evaluation/ai_metadata_20261030_verified'
ROUND1=ROOT/'data/generated/evaluation/enriched_poi_round1_20261028'
PHASE1=ROOT/'data/generated/evaluation/human_evaluation_phase1_20261029_single_rater_verified'
SDD=Path(r'C:\Users\Administrator\.codex\.chatgpt-projects\g-p-6a962829564c8191b974c172961babe5\week7_review\sdd.txt')
SRS=SDD.with_name('srs.txt')
sys.path.insert(0,str(HERE.parent/'ai_metadata_20261030'))
from evaluate_metadata import SparseLexical,clean,sparse_scores,digest,read_csv,write_csv,write_json,verify_legacy_content,protected_snapshot,FEATURES,score_individuals
from aggregation import aggregate,calibrate,expected_rank_relevance,utilities,utility_metrics


def make_groups():
    groups=[]
    types=['homogeneous','mild_disagreement','strong_disagreement','minority','polarized','hard_exclusion','sparse_metadata']
    for seed,size,kind in __import__('itertools').product([11,29,47],[2,3,4,6,10],types):
        rng=random.Random(seed+100*types.index(kind))
        base=rng.randrange(12);mate=base^1
        far=rng.choice([i for i in [0,2,6,10] if i//2!=base//2])
        if kind=='homogeneous':profiles=[base]*size
        elif kind=='mild_disagreement':profiles=[base if i%2==0 else mate for i in range(size)]
        elif kind=='minority':profiles=[base]*(size-1)+[far]
        elif kind=='polarized':profiles=[base]*(size//2)+[far]*(size-size//2)
        elif kind=='hard_exclusion':profiles=[6 if i%2==0 else 10 for i in range(size)]
        elif kind=='sparse_metadata':profiles=[6 if i%2==0 else 7 for i in range(size)]
        else:
            choices=[0,2,6,10];rng.shuffle(choices);profiles=[choices[i%4] for i in range(size)]
        groups.append({'group_id':f'{kind}_n{size}_s{seed}','type':kind,'size':size,'seed':seed,'members':[{'member_id':f'u{i:02d}','profile_index':p,'scenario_id':f'S{p+1:02d}'} for i,p in enumerate(profiles)],'excluded_features':['food'] if kind=='hard_exclusion' else [],'sparse_policy':'Remove descriptions from each member unmasked C top20 where present' if kind=='sparse_metadata' else 'Natural source missingness'})
    return groups


def mask_scores(raw,projection,lost):
    if raw.shape!=projection.shape or len(lost)!=raw.shape[1]:raise ValueError('Mask dimensions')
    return np.where(np.asarray(lost,dtype=bool)[None,:],projection,raw)


def validate_bundle(scores,profile_ids,poi_ids,expected_hash,actual_hash):
    if actual_hash!=expected_hash or len(set(poi_ids))!=len(poi_ids) or len(set(profile_ids))!=len(profile_ids):raise ValueError('Dataset/profile version mismatch')
    for a in scores.values():
        if a.shape!=(len(profile_ids),len(poi_ids)) or not np.isfinite(a).all():raise ValueError('Invalid frozen score matrix')


def freeze_config():
    return {'name':'Method C + GFAR controlled group simulation','date_korea':'2026-10-30','primary_scorers':['A','C'],'secondary_scorers':['B','D'],'aggregators':['AVG','AWM','GFAR'],'profile_seeds':[11,29,47],'sizes':[2,3,4,6,10],'candidate_top_L':100,'gfar_depth':20,'diagnostic_depth':100,'K':10,'primary_calibration':'percentile','primary_AWM_threshold':.5,'sensitivity_calibration':'minmax','sensitivity_AWM_thresholds':[.25,.5,.75],'tie_policy':'Raw member ranks: descending score, ascending full place_id. GFAR gains rounded12; calibrated mean descending then ascending ID. Members sorted by stable ID before arithmetic. Single-member and single-candidate SDD extensions.','shared_candidates':'Union of all member A-top100 and C-top100, identical for all primary and secondary configurations. Separate own-scorer unions diagnostic only.','calibration_note':'Percentile uses average exact-tie positions over all hard-eligible POIs mapped to 0..1 (constant=.5); minmax per member over same eligible universe (constant=0). Neither is empirical satisfaction calibration; no human labels fitted.','evaluation':'Two fixed assessors for every list: expected triangular top100 relevance from unmasked A and from unmasked C, averaging over whole exact-tie bands. Discounted list utility divided by fixed-K full-eligible ideal; empty lists N/A, shorter lists retain denominator. This is simulated rank utility, never independent NDCG.','mask_percentages':[0,10,25,50,75],'mask_seeds':[11,29,47,71,101],'mask_policy':'Exact previous new-metadata nested masks (1832 usable descriptions), same IDs/seeds; native C uses fixed metadata projections; compare B-backbone/C-tie ordinal fallback. Freeze evaluator at unmasked original C for every mask.','missing_policy':'Sparse groups additionally lose narratives of unmasked-C top20 per member; this is targeted stress under assumed relevance, not a human relevance claim.','numerical_readiness_rule':{'paired_assessor':'C_rank','shared_primary_only':True,'median_worst_member_gain_CGFAR_vs_CAVG_at_least':.02,'mean_rank_utility_retention_at_least':.95,'full_K_fraction_at_least':.95,'diagnostic_note':'Apply without tuning; separately report AWM valid-pair sample and empty-list selection bias.'},'production_adoption_gate':'Requires adequate independent group-level/relevance evidence. Historical one-human labels and synthetic simulations alone cannot satisfy this gate.','hard_filters':'Original two documented dataset exclusions already fixed in imported 3568 IDs, plus group explicit canonical food-feature exclusion; no inferred hours/budget/accessibility constraints.','gfar_reference':'https://doi.org/10.1145/3383313.3412232 Eq1/5; SDD9.2/9.3 boundaries and fixture','paper_and_SDD_differences':'Same normalized Borda/multiplicative unmet state. SDD adds ID/mean ties, L=1 and single-member rules. Production GFAR uses input-order ties and default depth20; SDD mock uses rounding12 and mean then ID. A here is production mean-active-feature scorer, NOT SDD sum-normalized survey formula.','SRS_SDD_role':'Read-only modifiable design references; no edits, production changes, itinerary claims or document immutability assumption.'}


def run(out):
    if out.exists():raise FileExistsError('Fresh output directory required')
    out.mkdir(parents=True);(out/'group_scores').mkdir()
    tracked=protected_snapshot();legacy=verify_legacy_content()
    before={str(p):digest(p) for folder in [META,ROUND1,PHASE1] for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    before.update({str(p):digest(p) for p in [SDD,SRS,ROOT/'services/mocks/group_rec.py',ROOT/'api/tests/test_group_rec.py']})
    config=freeze_config();write_json(out/'config.json',config)
    profiles=json.loads((ROUND1/'scenarios.json').read_text(encoding='utf-8'))
    model_inputs=[r for r in read_csv(META/'model_inputs.csv') if r['domain']=='enriched_catalogue'];ids=[r['place_id'] for r in model_inputs];pos={p:i for i,p in enumerate(ids)}
    canonical={r['place_id']:r for r in read_csv(ROOT/'src/ourney_travelsync/data/travelsync_final_places_v2.csv')}
    features=np.asarray([[float(canonical[p][f]) for f in FEATURES] for p in ids])
    cached=np.load(META/'scores_enriched_catalogue.npz',allow_pickle=False)
    matrices={k:cached[k] for k in cached.files}
    expected=json.loads((META/'manifest.json').read_text(encoding='utf-8'))['input_hashes']
    canonical_hash=expected[str(ROOT/'src/ourney_travelsync/data/travelsync_final_places_v2.csv')]
    validate_bundle(matrices,[p['scenario_id'] for p in profiles],ids,canonical_hash,digest(ROOT/'src/ourney_travelsync/data/travelsync_final_places_v2.csv'))
    groups=make_groups();write_json(out/'group_profiles.json',groups);write_json(out/'individual_profiles.json',profiles)
    masks=json.loads((META/'description_masks.json').read_text(encoding='utf-8'));write_json(out/'description_masks.json',masks)
    mask_sets={m['mask_id']:set(m['masked_place_ids']) for m in masks}
    write_csv(out/'poi_index.csv',[{'column':i,'place_id':pid,'feature_signature':''.join(str(int(v)) for v in features[i]),'description_present':model_inputs[i]['usable_description']} for i,pid in enumerate(ids)])
    np.savez_compressed(out/'full_profile_score_matrices.npz',**matrices)
    write_json(out/'pre_execution_freeze.json',{'config':digest(out/'config.json'),'groups':digest(out/'group_profiles.json'),'profiles':digest(out/'individual_profiles.json'),'mask':digest(out/'description_masks.json'),'score_matrices':digest(out/'full_profile_score_matrices.npz'),'scoring_source_hashes':{str(p):digest(p) for p in [META/'model_inputs.csv',META/'lexical_models.json',META/'scores_enriched_catalogue.npz',META/'source_validation.json']},'code_hashes':{str(p):digest(p) for p in HERE.glob('*.py')},'git_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),'created_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version,'executable':sys.executable,'platform':platform.platform(),'versions':{n:importlib.metadata.version(n) for n in ['numpy','scipy']},'decision_frozen_before_any_group_results':True})
    # Restore fitted lexical index; do not refit or invoke an embedding model.
    spec=json.loads((META/'lexical_models.json').read_text(encoding='utf-8'))['enriched_catalogue']['C']
    model=object.__new__(SparseLexical);model.vocab=spec['vocabulary'];model.index={v:i for i,v in enumerate(model.vocab)};model.idf=np.asarray(spec['idf'])
    prep_start=perf_counter();docs=model.encode([r['C_D_text'] for r in model_inputs]);prep_seconds=perf_counter()-prep_start
    queries=[clean(p['preference_ko']) for p in profiles];pref=np.asarray([[p['preference_vector'][f] for f in FEATURES] for p in profiles])
    start=perf_counter();recomputed=sparse_scores(model.encode(queries),docs);warm_profile_seconds=perf_counter()-start
    assert np.allclose(recomputed,matrices['C'],rtol=0,atol=1e-12)
    assert np.array_equal(score_individuals(pref,features),matrices['A'])
    write_json(out/'scoring_regression.json',{'C_max_absolute_error':float(np.abs(recomputed-matrices['C']).max()),'A_exact':True,'prior_historical_NDCG_source':str(META/'annotated_method_comparison.csv'),'fitted_index_restore_document_encoding_seconds':prep_seconds,'all12_query_encoding_and_scoring_seconds':warm_profile_seconds,'B_D_secondary':'Cached frozen matrices only; no encoding runtime claim'})
    result=[];runtime=[];mask_candidate_rows=[];run_count=0
    member_file=(out/'per_member_results.csv').open('w',encoding='utf-8-sig',newline='');member_writer=csv.DictWriter(member_file,fieldnames=['run_id','group_id','assessor','member_id','scenario_id','rank_utility','represented_in_top100_support']);member_writer.writeheader()
    candidate_file=gzip.open(out/'candidate_unions.jsonl.gz','wt',encoding='utf-8')
    trace_file=gzip.open(out/'selection_traces.jsonl.gz','wt',encoding='utf-8')
    exclusion=[]
    for gi,g in enumerate(groups):
        allowed=np.ones(len(ids),dtype=bool)
        for f in g['excluded_features']:allowed &= features[:,FEATURES.index(f)]==0
        ix=np.flatnonzero(allowed);eligible=[ids[i] for i in ix];lookup={p:i for i,p in enumerate(eligible)}
        for i in np.flatnonzero(~allowed):exclusion.append({'group_id':g['group_id'],'place_id':ids[i],'reason':'Explicit u00 feature exclusion: food','stage':'E1'})
        ps=[m['profile_index'] for m in g['members']];users=[m['member_id'] for m in g['members']]
        raw={m:matrices[m][np.ix_(ps,ix)].copy() for m in 'ABCD'}
        projections={m:matrices[m+'_metadata_projection'][np.ix_(ps,ix)] for m in ['C','D']}
        initial_loss=np.zeros(len(ix),dtype=bool)
        if g['type']=='sparse_metadata':
            for row in raw['C']:
                for i in sorted(range(len(eligible)),key=lambda i:(-row[i],eligible[i]))[:20]:
                    if model_inputs[ix[i]]['usable_description']=='True':initial_loss[i]=True
            raw['C']=mask_scores(raw['C'],projections['C'],initial_loss)
        np.savez_compressed(out/'group_scores'/f'{g["group_id"]}.npz',poi_ids=np.asarray(eligible),member_ids=np.asarray(users),profile_indices=np.asarray(ps),initial_targeted_description_loss=initial_loss,**raw)
        # Evaluators always refer to unmasked source scores, not the perturbed recommender.
        evaluators={m+'_rank':expected_rank_relevance(matrices[m][np.ix_(ps,ix)],100) for m in ['A','C']}
        timing={}
        for m in ['A','C']:
            start=perf_counter()
            if m=='A':score_individuals(pref[ps],features[ix])
            else:sparse_scores(model.encode([queries[p] for p in ps]),docs[ix])
            timing[m]=perf_counter()-start
        def union_for(scorers):
            start=perf_counter();chosen=set()
            for scores in scorers:
                for row in scores:chosen.update(sorted(range(len(eligible)),key=lambda i:(-row[i],eligible[i]))[:100])
            return sorted(chosen,key=lambda i:eligible[i]),perf_counter()-start
        shared,shared_seconds=union_for([raw['A'],raw['C']])
        candidate_file.write(json.dumps({'group_id':g['group_id'],'condition':'shared','poi_ids':[eligible[i] for i in shared]},ensure_ascii=False)+'\n')
        def execute(stage,scorer,algorithm,pool,score,calibration='percentile',threshold=.5,depth=20,mask_id='p0_s11',policy='native',generation_seconds=0,mask_percent=0):
            nonlocal run_count
            run_count+=1;rid=f'R{run_count:06d}';pool_ids=[eligible[i] for i in pool]
            start=perf_counter();scaled_full=calibrate(score,calibration);normalization_seconds=perf_counter()-start
            start=perf_counter();picked,traces,info=aggregate(score[:,pool],scaled_full[:,pool],pool_ids,users,algorithm,10,depth,threshold);aggregation_seconds=perf_counter()-start
            columns=[lookup[p] for p in picked]
            for t in traces:trace_file.write(json.dumps({'run_id':rid,'group_id':g['group_id'],**t},ensure_ascii=False)+'\n')
            raw_pool=score[:,pool];pairs=len(pool)*(len(pool)-1)//2
            ties=statistics.mean(sum(n*(n-1)//2 for n in Counter(row).values())/pairs for row in raw_pool) if pairs else None
            signatures={tuple(features[pos[p]]) for p in picked};category={canonical[p]['category_name'] for p in picked}
            for assessor,e in evaluators.items():
                utility=utilities(e,columns,10);metric=utility_metrics(utility)
                represented=[bool(len(columns) and (row[columns]>0).any()) for row in e]
                result.append({'run_id':rid,'group_id':g['group_id'],'type':g['type'],'size':g['size'],'seed':g['seed'],'stage':stage,'condition':'scorer_specific' if stage=='candidate_diagnostic' else 'shared_unmasked_primary_union' if stage=='masking' else 'shared','scorer':scorer,'aggregator':algorithm,'calibration':calibration,'AWM_threshold':threshold if algorithm=='AWM' else None,'gfar_depth':depth if algorithm=='GFAR' else None,'mask_id':mask_id,'mask_percent':mask_percent,'fallback_policy':policy,'assessor':assessor,'eligible_count':len(eligible),'candidate_count':len(pool),'candidate_fraction':len(pool)/len(eligible),'returned':len(picked),'shortage':10-len(picked),'AWM_rejected':info['awm_rejected'],'member_top100_support_coverage':statistics.mean(represented) if picked else None,'feature_signature_coverage':len(signatures),'category_taxonomy_coverage':len(category),'mean_individual_exact_tie_fraction':ties,'topk_ids':'|'.join(picked),'member_utilities_json':json.dumps(utility),**metric})
                for member,value,rep in zip(g['members'],utility,represented):member_writer.writerow({'run_id':rid,'group_id':g['group_id'],'assessor':assessor,'member_id':member['member_id'],'scenario_id':member['scenario_id'],'rank_utility':value,'represented_in_top100_support':rep if picked else None})
            runtime.append({'run_id':rid,'group_id':g['group_id'],'stage':stage,'scorer':scorer,'aggregator':algorithm,'size':g['size'],'individual_scoring_ms':timing.get(scorer,0)*1000,'scoring_mode':'Warm query+score on restored index' if scorer in ['A','C'] else 'Cached frozen matrix lookup; encoding excluded','candidate_generation_ms':generation_seconds*1000,'normalization_ms':normalization_seconds*1000,'aggregation_ms':aggregation_seconds*1000,'end_to_end_offline_ms':(timing.get(scorer,0)+generation_seconds+normalization_seconds+aggregation_seconds)*1000,'note':'Scores/candidates reused across aggregators; times are summed stage measurements, exclude evaluation metrics, fitting and document preprocessing. Masking uses saved score projections.'})
        for scorer in 'AC':
            for alg in ['AVG','AWM','GFAR']:execute('primary',scorer,alg,shared,raw[scorer],generation_seconds=shared_seconds)
            own,seconds=union_for([raw[scorer]]);candidate_file.write(json.dumps({'group_id':g['group_id'],'condition':scorer+'_specific','poi_ids':[eligible[i] for i in own]},ensure_ascii=False)+'\n')
            for alg in ['AVG','AWM','GFAR']:execute('candidate_diagnostic',scorer,alg,own,raw[scorer],generation_seconds=seconds)
            for tau in [.25,.75]:execute('normalization_sensitivity',scorer,'AWM',shared,raw[scorer],threshold=tau)
            for alg in ['AVG','GFAR']:execute('normalization_sensitivity',scorer,alg,shared,raw[scorer],calibration='minmax')
            for tau in [.25,.5,.75]:execute('normalization_sensitivity',scorer,'AWM',shared,raw[scorer],calibration='minmax',threshold=tau)
            execute('depth_sensitivity',scorer,'GFAR',shared,raw[scorer],depth=100)
        for scorer in 'BD':
            for alg in ['AVG','AWM','GFAR']:execute('secondary',scorer,alg,shared,raw[scorer])
        # Candidate union/evaluators frozen at the group's unmasked state for masking.
        candidate_refs={};rank_refs={}
        for mask in masks:
            lost=initial_loss|np.asarray([p in mask_sets[mask['mask_id']] for p in eligible])
            c=mask_scores(matrices['C'][np.ix_(ps,ix)],projections['C'],lost)
            backbone=np.vstack([__import__('aggregation').policy_backbone(brow,crow) for brow,crow in zip(raw['B'],c)])
            for policy,scores in [('C_native',c),('B_backbone_C_ties',backbone)]:
                current_orders=[sorted(range(len(eligible)),key=lambda i:(-row[i],eligible[i])) for row in scores]
                current_union=set(i for order in current_orders for i in order[:100]);percentiles=calibrate(scores)
                if mask['requested_percent']==0:candidate_refs[policy]=current_union;rank_refs[policy]=(current_orders,percentiles)
                ref_orders,ref_percentiles=rank_refs[policy];ref_union=candidate_refs[policy]
                correlations=[float(np.corrcoef(a,b)[0,1]) for a,b in zip(ref_percentiles,percentiles) if np.ptp(a)>0 and np.ptp(b)>0]
                mask_candidate_rows.append({'group_id':g['group_id'],'type':g['type'],'size':g['size'],'mask_id':mask['mask_id'],'mask_percent':mask['requested_percent'],'policy':policy,'recomputed_union_count':len(current_union),'recomputed_union_fraction':len(current_union)/len(eligible),'union_Jaccard_vs_policy0':len(current_union&ref_union)/len(current_union|ref_union),'outside_fixed_shared_union':len(current_union-set(shared)),'mean_individual_top10_retention':statistics.mean(len(set(a[:10])&set(b[:10]))/min(10,len(eligible)) for a,b in zip(ref_orders,current_orders)),'mean_individual_Spearman':statistics.mean(correlations) if correlations else None,'undefined_individual_correlations':len(users)-len(correlations),'note':'Separate candidate-generation diagnostic; primary masking recommendations retain fixed shared union'})
                for alg in ['AVG','AWM','GFAR']:execute('masking','C',alg,shared,scores,mask_id=mask['mask_id'],policy=policy,mask_percent=mask['requested_percent'])
        if gi%20==0:print(f'Groups complete: {gi+1}/{len(groups)}; runs={run_count}',flush=True)
    member_file.close();trace_file.close();candidate_file.close()
    write_csv(out/'per_group_results.csv',result);write_csv(out/'runtime_measurements.csv',runtime);write_csv(out/'hard_exclusions.csv',exclusion)
    write_csv(out/'mask_candidate_diagnostics.csv',mask_candidate_rows)
    human=human_evaluation(out,profiles,matrices,ids,features)
    write_json(out/'verification.json',{'status':'PASS','protected_scientific_sources_unchanged':all(digest(p)==v for p,v in before.items()),'tracked_files_unchanged':protected_snapshot()==tracked,'legacy_content':legacy,'protected_hashes':before,'runs':run_count,'per_group_assessor_rows':len(result),'groups':len(groups),'human_metric_rows':len(human),'fixed_evaluator_across_masking':True,'decision_config_precedes_results':True})
    assert all(digest(p)==v for p,v in before.items()) and protected_snapshot()==tracked
    from analysis_report import report
    report(out,result,runtime,human)
    write_json(out/'output_hashes.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
    print(json.dumps({'output':str(out),'groups':len(groups),'runs':run_count,'assessor_rows':len(result),'human_rows':len(human)},indent=2))


def human_evaluation(out,profiles,matrices,ids,features):
    """Only fully judged original blocks; synthetic composition, historical labels."""
    positions={p:i for i,p in enumerate(ids)};templates=read_csv(ROUND1/'results/annotation_R1.csv')
    grades={rid:{(r['scenario_id'],r['place_id']):int(r['relevance_0_3']) for r in read_csv(PHASE1/f'validated_adapter_{rid}.csv')} for rid in ['R1','R2']}
    rows=[];lists=[];trace_file=gzip.open(out/'human_selection_traces.jsonl.gz','wt',encoding='utf-8')
    for block in range(6):
        sids=[f'S{2*block+1:02d}',f'S{2*block+2:02d}'];candidates=sorted({r['place_id'] for r in templates if r['scenario_id']==sids[0]});ix=[positions[p] for p in candidates];k=min(3,len(ix))
        for kind,ps in [('mixed2',[2*block,2*block+1]),('minority3',[2*block,2*block,2*block+1]),('polarized4',[2*block,2*block,2*block+1,2*block+1])]:
            users=[f'u{i:02d}' for i in range(len(ps))];gid=f'human_block{block}_{kind}'
            for scorer in 'AC':
                raw=matrices[scorer][np.ix_(ps,ix)];scaled=calibrate(raw,'percentile')
                for algorithm in ['AVG','AWM','GFAR']:
                    picked,trace,info=aggregate(raw,scaled,candidates,users,algorithm,k,20,.5)
                    for item in trace:trace_file.write(json.dumps({'group_id':gid,'scorer':scorer,'aggregator':algorithm,**item})+'\n')
                    lists.append({'group_id':gid,'scorer':scorer,'aggregator':algorithm,'candidate_ids':'|'.join(candidates),'topk_ids':'|'.join(picked),'K':k,'returned':len(picked)})
                    for rid,label in grades.items():
                        values=[]
                        for u,p in enumerate(ps):
                            sid=f'S{p+1:02d}';g={pid:label[(sid,pid)] for pid in candidates}
                            gains={pid:2**value-1 for pid,value in g.items()};discount=1/np.log2(np.arange(2,k+2));ideal=float(np.asarray(sorted(gains.values(),reverse=True)[:k])@discount)
                            ndcg=float(np.asarray([gains[pid] for pid in picked])@discount[:len(picked)])/ideal if picked and ideal else None
                            values.append(ndcg)
                            rows.append({'group_id':gid,'block':block,'type':kind,'size':len(ps),'scorer':scorer,'aggregator':algorithm,'sheet_id':rid,'member_id':users[u],'scenario_id':sid,'K':k,'returned':len(picked),'human_ndcg':ndcg,'zero_ideal_DCG':ideal==0,'note':'Realized group order; no averaging across sequential GFAR ties. Same historical labels from one person, updated metadata not re-adjudicated; restricted fully judged block.'})
    trace_file.close();write_csv(out/'human_per_member_metrics.csv',rows);write_csv(out/'human_group_lists.csv',lists)
    return rows


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,default=ROOT/'data/generated/evaluation/c_gfar_20261030');a=p.parse_args();run(a.out.resolve())
