"""Tables and conclusions; no parameter choices are made from the results."""
from collections import defaultdict
import json,statistics
import numpy as np


def avg(xs):
    x=[float(v) for v in xs if v is not None and v!=''];return statistics.mean(x) if x else None


def fmt(v):return 'N/A' if v is None else f'{v:.6f}' if isinstance(v,float) else str(v)


def table(fields,rows):
    return ['| '+' | '.join(fields)+' |','| '+' | '.join(['---']*len(fields))+' |']+['| '+' | '.join(fmt(v).replace('|','\\|') for v in r)+' |' for r in rows]


def report(out,rows,runtime,human):
    from experiment import write_csv,write_json
    primary=[r for r in rows if r['stage']=='primary'];grouped=defaultdict(list)
    for r in primary:grouped[(r['scorer'],r['aggregator'],r['assessor'])].append(r)
    summary=[]
    for (scorer,alg,assessor),rs in sorted(grouped.items()):
        summary.append({'scorer':scorer,'aggregator':alg,'assessor':assessor,'groups':len(rs),'nonempty_groups':sum(r['returned']>0 for r in rs),'full_K_groups':sum(r['returned']==10 for r in rs),'mean_minimum_utility':avg([r['minimum_utility'] for r in rs]),'mean_member_utility':avg([r['mean_utility'] for r in rs]),'mean_utility_gap':avg([r['utility_gap'] for r in rs]),'mean_utility_std':avg([r['utility_std'] for r in rs]),'mean_Jain':avg([r['jain'] for r in rs]),'mean_representation_coverage':avg([r['member_top100_support_coverage'] for r in rs]),'mean_returned':avg([r['returned'] for r in rs]),'note':'Conditional utility means exclude N/A empty lists; coverage counts must accompany comparisons'})
    write_csv(out/'primary_summary.csv',summary)
    pairs=[]
    lookup={(r['group_id'],r['scorer'],r['aggregator'],r['assessor']):r for r in primary}
    contrasts=[('C','GFAR','C','AVG'),('C','GFAR','C','AWM'),('C','GFAR','A','GFAR'),('C','GFAR','A','AVG')]
    for ls,la,rs,ra in contrasts:
        for assessor in ['A_rank','C_rank']:
            for gid in sorted({r['group_id'] for r in primary}):
                a,b=lookup[(gid,ls,la,assessor)],lookup[(gid,rs,ra,assessor)]
                valid=a['mean_utility'] is not None and b['mean_utility'] is not None
                pairs.append({'contrast':f'{ls}+{la} vs {rs}+{ra}','group_id':gid,'type':a['type'],'size':a['size'],'assessor':assessor,'paired_valid':valid,'left_returned':a['returned'],'right_returned':b['returned'],'minimum_delta':a['minimum_utility']-b['minimum_utility'] if valid else None,'mean_delta':a['mean_utility']-b['mean_utility'] if valid else None,'gap_delta':a['utility_gap']-b['utility_gap'] if valid else None,'relative_minimum_change':(a['minimum_utility']-b['minimum_utility'])/b['minimum_utility'] if valid and b['minimum_utility'] else None,'relative_mean_change':(a['mean_utility']-b['mean_utility'])/b['mean_utility'] if valid and b['mean_utility'] else None,'left_minimum':a['minimum_utility'],'right_minimum':b['minimum_utility']})
    write_csv(out/'paired_contrasts.csv',pairs)
    contrast_summary=[]
    for contrast in sorted({r['contrast'] for r in pairs}):
        for assessor in ['A_rank','C_rank']:
            rs=[r for r in pairs if r['contrast']==contrast and r['assessor']==assessor];valid=[r for r in rs if r['paired_valid']]
            contrast_summary.append({'contrast':contrast,'assessor':assessor,'total_pairs':len(rs),'valid_pairs':len(valid),'mean_minimum_delta':avg([r['minimum_delta'] for r in valid]),'median_minimum_delta':statistics.median(r['minimum_delta'] for r in valid) if valid else None,'mean_mean_delta':avg([r['mean_delta'] for r in valid]),'mean_gap_delta':avg([r['gap_delta'] for r in valid]),'mean_relative_mean_change':avg([r['relative_mean_change'] for r in valid]),'improved_minimum':sum(r['minimum_delta']>1e-12 for r in valid),'tied_minimum':sum(abs(r['minimum_delta'])<=1e-12 for r in valid),'worse_minimum':sum(r['minimum_delta'] < -1e-12 for r in valid)})
    write_csv(out/'contrast_summary.csv',contrast_summary)
    strata=[]
    for dimension in ['type','size']:
        for value in sorted({r[dimension] for r in primary}):
            for (scorer,alg,assessor),rs in sorted(grouped.items()):
                selected=[r for r in rs if r[dimension]==value]
                strata.append({'dimension':dimension,'value':value,'scorer':scorer,'aggregator':alg,'assessor':assessor,'groups':len(selected),'nonempty':sum(r['returned']>0 for r in selected),'mean_minimum':avg([r['minimum_utility'] for r in selected]),'mean_utility':avg([r['mean_utility'] for r in selected]),'mean_gap':avg([r['utility_gap'] for r in selected]),'mean_returned':avg([r['returned'] for r in selected])})
    write_csv(out/'by_type_and_size.csv',strata)
    mask_summary=[]
    for pct in [0,10,25,50,75]:
        for policy in ['C_native','B_backbone_C_ties']:
            for alg in ['AVG','AWM','GFAR']:
                rs=[r for r in rows if r['stage']=='masking' and r['mask_percent']==pct and r['fallback_policy']==policy and r['aggregator']==alg and r['assessor']=='C_rank']
                retention=[];jaccard=[]
                refs={r['group_id']:r for r in rows if r['stage']=='masking' and r['mask_percent']==0 and r['fallback_policy']==policy and r['aggregator']==alg and r['assessor']=='C_rank'}
                for r in rs:
                    a=set(refs[r['group_id']]['topk_ids'].split('|'))- {''};b=set(r['topk_ids'].split('|'))-{''}
                    if a:retention.append(len(a&b)/len(a))
                    if a|b:jaccard.append(len(a&b)/len(a|b))
                mask_summary.append({'mask_percent':pct,'policy':policy,'aggregator':alg,'query_group_seed_rows':len(rs),'nonempty':sum(r['returned']>0 for r in rs),'mean_minimum_utility':avg([r['minimum_utility'] for r in rs]),'mean_utility':avg([r['mean_utility'] for r in rs]),'mean_gap':avg([r['utility_gap'] for r in rs]),'mean_Jain':avg([r['jain'] for r in rs]),'mean_topk_retention':avg(retention),'mean_topk_Jaccard':avg(jaccard),'mean_candidate_count':avg([r['candidate_count'] for r in rs]),'mean_returned':avg([r['returned'] for r in rs]),'note':'Evaluator and candidate union fixed; empty baseline retention N/A; no stability=accuracy claim'})
    write_csv(out/'missing_description_summary.csv',mask_summary)
    sensitivity=[]
    for stage in ['candidate_diagnostic','normalization_sensitivity','depth_sensitivity','secondary']:
        buckets=defaultdict(list)
        for r in rows:
            if r['stage']==stage:buckets[(r['scorer'],r['aggregator'],r['calibration'],r['AWM_threshold'],r['gfar_depth'],r['assessor'])].append(r)
        for keys,rs in buckets.items():
            sensitivity.append(dict(zip(['scorer','aggregator','calibration','AWM_threshold','gfar_depth','assessor'],keys),stage=stage,groups=len(rs),nonempty=sum(r['returned']>0 for r in rs),full_K=sum(r['returned']==10 for r in rs),mean_minimum=avg([r['minimum_utility'] for r in rs]),mean_utility=avg([r['mean_utility'] for r in rs]),mean_returned=avg([r['returned'] for r in rs])))
    write_csv(out/'sensitivity_summary.csv',sensitivity)
    human_groups=defaultdict(list)
    for r in human:human_groups[(r['group_id'],r['scorer'],r['aggregator'],r['sheet_id'])].append(r)
    hg=[]
    for (gid,scorer,alg,sheet),rs in human_groups.items():
        values=[r['human_ndcg'] for r in rs];valid=all(v is not None for v in values)
        hg.append({'group_id':gid,'scorer':scorer,'aggregator':alg,'sheet_id':sheet,'size':len(rs),'returned':rs[0]['returned'],'mean_human_ndcg':avg(values) if valid else None,'minimum_human_ndcg':min(values) if valid else None,'note':'18 synthetic group compositions in six fully labelled blocks; one human, not independent group satisfaction evidence'})
    write_csv(out/'human_group_metrics.csv',hg)
    human_summary=[]
    for scorer in 'AC':
        for alg in ['AVG','AWM','GFAR']:
            rs=[r for r in hg if r['scorer']==scorer and r['aggregator']==alg]
            human_summary.append({'scorer':scorer,'aggregator':alg,'group_sheet_cases':len(rs),'nonempty_cases':sum(r['returned']>0 for r in rs),'mean_group_mean_human_ndcg':avg([r['mean_human_ndcg'] for r in rs]),'mean_group_minimum_human_ndcg':avg([r['minimum_human_ndcg'] for r in rs]),'mean_returned':avg([r['returned'] for r in rs])})
    write_csv(out/'human_summary.csv',human_summary)
    rt=[]
    for scorer in 'AC':
        for alg in ['AVG','AWM','GFAR']:
            rs=[r for r in runtime if r['stage']=='primary' and r['scorer']==scorer and r['aggregator']==alg]
            rt.append({'scorer':scorer,'aggregator':alg,'samples':len(rs),**{field+'_'+suffix:float(np.percentile([r[field] for r in rs],percentile)) for field in ['individual_scoring_ms','candidate_generation_ms','aggregation_ms','end_to_end_offline_ms'] for suffix,percentile in [('median',50),('p95',95)]}})
    write_csv(out/'runtime_summary.csv',rt)
    readiness=next(r for r in contrast_summary if r['contrast']=='C+GFAR vs C+AVG' and r['assessor']=='C_rank')
    cg=grouped[('C','GFAR','C_rank')];ca=grouped[('C','AVG','C_rank')]
    mean_retention=avg([r['mean_utility'] for r in cg])/avg([r['mean_utility'] for r in ca])
    numeric=readiness['median_minimum_delta']>=.02 and mean_retention>=.95 and sum(r['returned']==10 for r in cg)/len(cg)>=.95
    decision={'conclusion':'Insufficient evidence; more evaluation required','numerical_readiness_pass':numeric,'median_worst_member_gain_C_rank':readiness['median_minimum_delta'],'mean_utility_retention_C_rank':mean_retention,'full_K_fraction':sum(r['returned']==10 for r in cg)/len(cg),'independent_production_evidence_gate':False,'reason':'Simulation and historical labels from one person cannot establish real group satisfaction; retain application-facing architecture unchanged.'}
    write_json(out/'decision.json',decision)
    worst=sorted([r for r in pairs if r['paired_valid']],key=lambda r:r['minimum_delta'])[:30];write_csv(out/'worst_cases.csv',worst)
    doc=['# Method C + GFAR group recommendation experiment','',
         '## Decision','',decision['conclusion']+'. Production integration and SRS/SDD documents remain unchanged. Numerical-readiness gate passed: '+str(numeric)+'. This is an offline controlled simulation, not measured real-world or demographic fairness.','',
         'The six required configurations were run on 105 frozen groups (seven conflict/stress types × five sizes × three seeds), with the same eligible universe, hard exclusions, shared candidate union and K=10. Secondary B/D, scorer-specific candidate generation, calibration/threshold/depth sensitivities and nested masking are separate stages. Original 12 profile texts/vectors and the full-catalogue enriched model are reused without fitting on human grades.','',
         '## GFAR definition and recovered references','',
         'The [GFAR paper](https://cora.ucc.ie/server/api/core/bitstreams/a40b7c99-11c9-46c9-a5af-be7b93f66b61/content), equations 1 and 5, defines normalized Borda relevance from each member ranking and a greedy gain equal to sum_u p(u,i) q(u), with q initially 1 and multiplied by 1−p after selection. Our adapter implements those equations and records every state transition. Personal rank ties use IDs; gain ties use rounded12 gain, calibrated mean then ID, as in the SDD mock. Single-member/single-candidate boundaries are SDD extensions. The paper specifies no corresponding implementation tie policy.','',
         'The paper example sequence i1,i4,i2 is reproduced. Its extracted Table2 reports third-step gain 2/27, while substitution into equation5 gives 2/9; the test follows equation5, documents this source discrepancy and does not alter the recurrence. The unambiguous SDD A,C,B fixture and gains 4/3,2/3,1/3 are reproduced independently against services/mocks/group_rec.py.','',
         'Current production GFAR uses normalized Borda with default depth20 and input-order ties. The standard pipeline supplies SHA256-seeded identity order. The SDD instead calls for ID/mean ties. This experiment names its variant SDD-ties GFAR; no production code was refactored. Production A averages survey preference over active features; the SDD mock sums survey-normalized weights without dividing by active feature count. Those are different scorers, not a scale-only change. The production A scorer is retained here for historical A/B/C/D consistency.','',
         'SRS/SDD v1.1 were found as existing extracted text in the project mirror. Sections 8/9, SDD deterministic fixture and SRS FR-GFAR-01 were inspected. They are read-only design references for this task, not treated as immutable architecture requirements. This recommendation-only experiment makes no itinerary feasibility or E2/E3 claim.','',
         '## Frozen methods and evaluation','',
         'A/C full member×POI matrices are preserved per group; B/D cached matrices are secondary. Nine enriched conflict rows have already had new attributes quarantined and the two documented identity exclusions remain absent from the 3,568-ID universe. Explicit food-feature bans apply to every scorer before candidate generation, with reasons saved. No operating, accessibility or price facts are invented.','',
         'Candidate Top-L=100; primary shared union includes every member A-top100 and C-top100. AVG/AWM/GFAR see that exact union. Own-scorer union results are diagnostic and cannot establish aggregator improvements. GFAR depth=20, with depth100 sensitivity. All score ties and cutoff identities are deterministic.','',
         'AVG/AWM use either exact-tie average percentile proxies on 0..1 (primary; constant rows=.5) or per-member minmax proxies (sensitivity; constant rows=0). AWM thresholds=.25/.5/.75 are ordinal acceptance rules, not calibrated satisfaction guarantees. AWM rejects strictly below threshold, preserves shorter/empty lists, and never fills. Positive cosine is not satisfaction. No threshold was fitted on the historical judgments.','',
         'Every recommendation list is assessed twice: under fixed unmasked A-derived rank relevance and fixed unmasked C-derived rank relevance. Expected triangular top100 weight averages across entire exact score ties; large tie bands do not receive ID-specific evaluator advantages. Member utility is position-discounted expected weight divided by the fixed-K ideal over all eligible POIs. Short lists retain this denominator; empty utility is N/A. Jain is N/A for all-zero utilities. Representation means any positive expected top100 support; utility≥.5 coverage is an explicitly assumed threshold. These metrics are simulation assumptions, not independent relevance labels or calibrated satisfaction. Constant uninformative rankings may assess all complete lists equally highly.','',
         'The adoption/readiness rule was frozen before group results: median paired worst-member gain ≥.02 under C_rank against C+AVG, mean utility retention ≥95%, and full-K rate ≥95%. Production adoption additionally requires adequate independent group evidence, unavailable here. Scorer superiority is never inferred solely from its own evaluator.','',
         '## Primary simulation results','']
    for assessor in ['A_rank','C_rank']:
        doc += [f'### Fixed {assessor} evaluator','']
        doc += table(['Configuration','Mean worst-member','Mean relevance utility','Mean disparity','Mean Jain','Nonempty / 105','Full K / 105'],[[r['scorer']+'+'+r['aggregator'],r['mean_minimum_utility'],r['mean_member_utility'],r['mean_utility_gap'],r['mean_Jain'],r['nonempty_groups'],r['full_K_groups']] for r in summary if r['assessor']==assessor])
    doc += ['','Utility means for AWM are conditional on returned lists; empty/short counts are essential and cannot be hidden by an apparently high conditional mean. Complete per-member distributions are in per_member_results.csv; paired_contrasts.csv includes absolute/relative changes and denominators.','', '## Fairness–relevance primary contrasts','']
    doc += table(['Contrast','Assessor','Valid pairs','Mean minimum Δ','Median minimum Δ','Mean relevance Δ','Mean disparity Δ','Better/tie/worse minimum'],[[r['contrast'],r['assessor'],r['valid_pairs'],r['mean_minimum_delta'],r['median_minimum_delta'],r['mean_mean_delta'],r['mean_gap_delta'],f"{r['improved_minimum']}/{r['tied_minimum']}/{r['worse_minimum']}"] for r in contrast_summary])
    doc += ['', '## Results by conflict and size','', 'All type/size strata are exported in by_type_and_size.csv. Below is C_rank for the fixed-C comparison; each type has 15 groups and each size 21 groups. Preference clusters are fixed within seed/type across sizes; minority fraction changes with size by design.','']
    doc += table(['Dimension','Value','Aggregator','Mean worst-member','Mean relevance','Mean returned'],[[r['dimension'],r['value'],r['aggregator'],r['mean_minimum'],r['mean_utility'],r['mean_returned']] for r in strata if r['scorer']=='C' and r['assessor']=='C_rank'])
    doc += ['', '## Cases where GFAR or Method C is worse','', 'Worst paired minimum-utility losses across all four contrasts and both fixed evaluators are preserved, not filtered to favourable cases. A/C evaluator disagreement indicates dependence on assumed relevance and cannot establish which scorer is correct.','']
    doc += table(['Contrast','Group','Assessor','Worst-member Δ','Mean relevance Δ','Returned L/R'],[[r['contrast'],r['group_id'],r['assessor'],r['minimum_delta'],r['mean_delta'],f"{r['left_returned']}/{r['right_returned']}"] for r in worst[:12]])
    doc += ['', '## Restricted historical human-label assessment','', 'Eighteen synthetic compositions use the six original fully judged candidate blocks: mixed pairs, majority/minority triples and polarized groups of four. Every member has a historical label for every candidate. All configurations use identical block candidates and K=min(3,block size). Per-member NDCG is calculated from actual group output order with graded gain 2^grade−1 and logarithmic discount. Short lists retain the fixed ideal; empty lists are N/A. Realized deterministic group ordering is used, not a static tie-expected score on sequential GFAR gains. This differs from original individual tie-expected NDCG and must not be conflated.','', 'These grades come from two sheets completed by ONE annotator. Group compositions are constructed, source summaries changed after annotation, and no group satisfaction was collected. Stable-ID ties can influence these small-block results, especially for A. The restricted results provide limited scorer-independent checks rather than independent population validation.','']
    doc += table(['Configuration','Group/sheet cases','Nonempty','Mean member NDCG','Mean worst-member NDCG','Mean returned'],[[r['scorer']+'+'+r['aggregator'],r['group_sheet_cases'],r['nonempty_cases'],r['mean_group_mean_human_ndcg'],r['mean_group_minimum_human_ndcg'],r['mean_returned']] for r in human_summary])
    doc += ['', '## Missing descriptions and fallback','', 'The exact previous new-metadata nested masks at 0/10/25/50/75%, seeds11/29/47/71/101 are reused. Sparse groups additionally mask unmasked-C top20 narratives as a targeted assumed-relevance stress. Candidate unions and both evaluation functions stay fixed across perturbations, so this is a controlled ranking/aggregation robustness test, not a changing-ground-truth comparison. Candidate coverage is held constant by design; scorer-specific generation is measured separately. Native C uses saved fixed-model metadata projections. The B-backbone/C-tie fallback is lexicographic and never sums raw B/C values.','']
    doc += table(['Mask %','Policy','Aggregator','Mean worst-member','Mean relevance','Gap','TopK retention','Mean returned'],[[r['mask_percent'],r['policy'],r['aggregator'],r['mean_minimum_utility'],r['mean_utility'],r['mean_gap'],r['mean_topk_retention'],r['mean_returned']] for r in mask_summary if r['aggregator'] in ['AVG','GFAR']])
    doc += ['', '## Normalization, threshold, depth and candidate sensitivity','', 'sensitivity_summary.csv separates candidate-generation, normalization/AWM-threshold, GFAR-depth and secondary-B/D conditions. Do not attribute changed candidate recall to group aggregation or call the B/D cached encoding latency a measured service runtime. Calibration can change AVG/AWM and GFAR mean-based ties; this uncertainty is part of the result.','']
    doc += table(['Stage','Scorer','Algorithm','Calibration','Threshold/depth','Nonempty','Mean worst-member','Mean relevance'],[[r['stage'],r['scorer'],r['aggregator'],r['calibration'],r['AWM_threshold'] if r['aggregator']=='AWM' else r['gfar_depth'],r['nonempty'],r['mean_minimum'],r['mean_utility']] for r in sensitivity if r['assessor']=='C_rank' and r['scorer']=='C'])
    doc += ['', '## Runtime and reproducibility','', 'Runtime uses restored fixed TF-IDF vectors and warm queries; no fit or embedding inference is included. Individual A/C scoring, shared-candidate generation, normalization and aggregation are timed separately. End-to-end offline time is the sum of those measured stages; evaluation, file export, index restoration and document preprocessing are excluded and separately disclosed. The shared union requires A and C candidate generation for control; it is experimental overhead, not a deployed single-scorer service benchmark. Masking uses cached score projections. Runtime median/p95 and samples are in runtime_summary.csv.','']
    doc += table(['Configuration','Samples','Score median ms','Candidate median ms','Aggregate median ms','Offline median ms','Offline p95 ms'],[[r['scorer']+'+'+r['aggregator'],r['samples'],r['individual_scoring_ms_median'],r['candidate_generation_ms_median'],r['aggregation_ms_median'],r['end_to_end_offline_ms_median'],r['end_to_end_offline_ms_p95']] for r in rt])
    doc += ['', '## Explicit answers','',
            'Q1: Read the fixed-scorer C+GFAR versus C+AVG/AWM paired contrasts above. Improvements and losses are assessor-dependent and AWM empty-list pairs have smaller denominators. GFAR does not guarantee better worst-member utility for every group.','',
            'Q2: A/C cross-scorer results under A_rank/C_rank are circular if treated as accuracy evidence. Use the restricted human table for a limited independent-from-scorer signal; it does not establish broad individually relevant recommendations.','',
            'Q3: Numerical readiness under the predeclared thresholds is '+str(numeric)+f'; median worst-member Δ={readiness["median_minimum_delta"]:.6f}, mean-utility retention={mean_retention:.6f}. Acceptable independent real-world relevance is not established.','',
            'Q4: Type/size strata, worst cases and missing-data rows identify favourable and unfavourable conditions descriptively. Repeated seeds and reused queries are correlated perturbations, not independent user observations; no significance is claimed.','',
            'Q5: Evidence is insufficient to replace the original A+GFAR architecture. Keep production unchanged and retain C+GFAR as an experimental comparison only. More independent relevance/group feedback and score calibration are needed; itinerary optimization is outside this experiment.','',
            'Outputs include reusable aggregation.py, frozen group profiles, config/decision rules, complete score matrices, per-member/group results, GFAR JSONL traces, candidate/exclusion records, paired/stratified/sensitivity/human summaries, runtime and plot artifacts. Source/code hashes, environment, dirty state and exact scientific-input preservation are recorded. Automated test and final consistency logs are attached after execution. No source dataset, prior experiment, SRS/SDD or app-facing recommendation code is modified.','']
    (out/'REPORT.md').write_text('\n'.join(doc)+'\n',encoding='utf-8')
