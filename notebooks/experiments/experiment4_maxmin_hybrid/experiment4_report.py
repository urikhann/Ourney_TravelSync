"""Summary tables and report derived only from executed rows (unique module name)."""
from collections import defaultdict
import json,statistics
import numpy as np
from experiment4 import table,read,js,mean,METHODS,LAMBDAS

def grouped(rows,keys):
    d=defaultdict(list)
    for r in rows:d[tuple(r[k] for k in keys)].append(r)
    return d

def summaries(rows,keys):
    result=[]
    for key,rs in grouped(rows,keys).items():
        valid=[r for r in rs if r['minimum_utility'] is not None]
        result.append({**dict(zip(keys,key)),'groups':len(rs),'valid_groups':len(valid),'full_K_groups':sum(r['returned']==r['K'] for r in rs),'mean_minimum':mean([r['minimum_utility'] for r in valid]),'median_minimum':statistics.median([r['minimum_utility'] for r in valid]) if valid else None,'mean_member':mean([r['mean_utility'] for r in valid]),'mean_gap':mean([r['utility_gap'] for r in valid]),'mean_std':mean([r['utility_std'] for r in valid]),'mean_jain':mean([r['jain'] for r in valid]),'mean_support_coverage':mean([r['member_support_coverage'] for r in rs]),'mean_returned':mean([r['returned'] for r in rs]),'mean_candidate_count':mean([r['candidate_count'] for r in rs])})
    return result

def md(rows,fields):
    lines=['| '+' | '.join(fields)+' |','| '+' | '.join(['---']*len(fields))+' |']
    for r in rows:
        lines.append('| '+' | '.join('N/A' if r.get(f) is None else f'{r[f]:.6f}' if isinstance(r[f],float) else str(r[f]) for f in fields)+' |')
    return '\n'.join(lines)

def generate(out,rows,times):
    primary=[r for r in rows if r['stage']=='primary'];summary=summaries(primary,['assessor','method']);table(out/'primary_summary.csv',summary)
    table(out/'by_type_and_size.csv',summaries(primary,['assessor','method','type','size']))
    table(out/'by_type.csv',summaries(primary,['assessor','method','type']))
    table(out/'by_size.csv',summaries(primary,['assessor','method','size']))
    table(out/'sensitivity_summary.csv',summaries([r for r in rows if r['stage']=='sensitivity'],['assessor','method','sensitivity']))
    maskrows=[r for r in rows if r['stage']=='masking']
    reference={(r['group_id'],r['method'],r['assessor'],r['policy']):r for r in maskrows if r['mask_percent']==0}
    for r in maskrows:
        ref=reference[r['group_id'],r['method'],r['assessor'],r['policy']];a=set(r['topk_ids'].split('|'));b=set(ref['topk_ids'].split('|'));r['retention']=len(a&b)/len(b) if b else None
    masking=summaries(maskrows,['assessor','method','policy','mask_percent'])
    for s in masking:
        rs=[r for r in maskrows if all(r[k]==s[k] for k in ['assessor','method','policy','mask_percent'])];s['mean_topk_retention']=mean([r['retention'] for r in rs])
    table(out/'masking_summary.csv',masking)
    baselines={(r['group_id'],r['assessor']):r for r in primary if r['method']=='C+AVG'}
    contrasts=[]
    for r in primary:
        b=baselines[r['group_id'],r['assessor']];a=r['topk_ids'].split('|') if r['topk_ids'] else [];bb=b['topk_ids'].split('|');common=set(a)&set(bb)
        correlation=float(np.corrcoef([a.index(p) for p in sorted(common)],[bb.index(p) for p in sorted(common)])[0,1]) if len(common)>1 else None
        contrasts.append({'group_id':r['group_id'],'type':r['type'],'size':r['size'],'assessor':r['assessor'],'method':r['method'],'delta_fairness':r['minimum_utility']-b['minimum_utility'] if r['minimum_utility'] is not None else None,'delta_relevance':r['mean_utility']-b['mean_utility'] if r['mean_utility'] is not None else None,'topk_retention':len(common)/len(bb),'jaccard':len(common)/len(set(a)|set(bb)),'shared_item_order_spearman':correlation,'shared_items':len(common),'returned':r['returned'],'baseline_returned':b['returned'],'candidate_fraction':r['candidate_count']/r['eligible_count'],'exact_tie_fraction':r['exact_tie_fraction']})
    table(out/'paired_contrasts.csv',contrasts)
    contrast_summary=[]
    for (assessor,method),rs in grouped(contrasts,['assessor','method']).items():
        valid=[r for r in rs if r['delta_fairness'] is not None]
        contrast_summary.append({'assessor':assessor,'method':method,'valid_pairs':len(valid),'delta_fairness':mean([r['delta_fairness'] for r in valid]),'delta_relevance':mean([r['delta_relevance'] for r in valid]),'fairness_wins':sum(r['delta_fairness']>1e-12 for r in valid),'fairness_losses':sum(r['delta_fairness']< -1e-12 for r in valid),'fairness_ties':sum(abs(r['delta_fairness'])<=1e-12 for r in valid),'mean_topk_retention':mean([r['topk_retention'] for r in rs]),'mean_jaccard':mean([r['jaccard'] for r in rs])})
    table(out/'contrast_summary.csv',contrast_summary)
    # Fair Pareto comparison: retain full K in all 105 groups, matching universe.
    pareto=[]
    for assessor in ['A_rank','C_rank']:
        rs=[r for r in summary if r['assessor']==assessor and r['full_K_groups']==105]
        for r in rs:
            dominators=[q['method'] for q in rs if q['mean_minimum']>=r['mean_minimum']-1e-12 and q['mean_member']>=r['mean_member']-1e-12 and (q['mean_minimum']>r['mean_minimum']+1e-12 or q['mean_member']>r['mean_member']+1e-12)]
            pareto.append({'assessor':assessor,'method':r['method'],'nondominated':not dominators,'dominators':'|'.join(dominators),'mean_minimum':r['mean_minimum'],'mean_member':r['mean_member']})
    table(out/'pareto.csv',pareto)
    table(out/'worst_groups.csv',sorted([r for r in contrasts if r['method']=='C+Max-Min' and r['assessor']=='C_rank'],key=lambda r:r['delta_fairness'])[:15])
    runtime=[]
    for (stage,method,size),rs in grouped(times,['stage','method','size']).items():
        runtime.append({'stage':stage,'method':method,'size':size,'runs':len(rs),'aggregation_median_ms':float(np.median([r['aggregation_ms'] for r in rs])),'aggregation_p95_ms':float(np.percentile([r['aggregation_ms'] for r in rs],95)),'warm_offline_median_ms':float(np.median([r['warm_offline_sum_ms'] for r in rs])),'warm_offline_p95_ms':float(np.percentile([r['warm_offline_sum_ms'] for r in rs],95))})
    table(out/'runtime_summary.csv',runtime)
    runtime_total=[]
    for (method,),rs in grouped([r for r in times if r['stage']=='primary'],['method']).items():
        runtime_total.append({'method':method,'aggregation_median_ms':float(np.median([r['aggregation_ms'] for r in rs])),'aggregation_p95_ms':float(np.percentile([r['aggregation_ms'] for r in rs],95)),'warm_offline_median_ms':float(np.median([r['warm_offline_sum_ms'] for r in rs])),'warm_offline_p95_ms':float(np.percentile([r['warm_offline_sum_ms'] for r in rs],95))})
    gf=next(r for r in runtime_total if r['method']=='C+GFAR')['aggregation_median_ms'];av=next(r for r in runtime_total if r['method']=='C+AVG')['aggregation_median_ms']
    for r in runtime_total:r.update(relative_to_GFAR=r['aggregation_median_ms']/gf,relative_to_AVG=r['aggregation_median_ms']/av)
    table(out/'runtime_primary.csv',runtime_total)
    human=read(out/'human_per_member_metrics.csv');hg=[]
    for (gid,method,sheet),rs in grouped(human,['group_id','method','sheet']).items():
        values=[float(r['human_ndcg']) for r in rs if r['human_ndcg']]
        hg.append({'group_id':gid,'method':method,'sheet':sheet,'valid_members':len(values),'minimum':min(values) if len(values)==len(rs) else None,'mean':mean(values) if len(values)==len(rs) else None})
    table(out/'human_group_metrics.csv',hg)
    hs=[]
    for (method,sheet),rs in grouped(hg,['method','sheet']).items():hs.append({'method':method,'sheet':sheet,'groups':len(rs),'valid_groups':sum(r['minimum'] is not None for r in rs),'mean_minimum':mean([r['minimum'] for r in rs]),'mean_member':mean([r['mean'] for r in rs])})
    table(out/'human_summary.csv',hs)
    hbase={(r['group_id'],r['sheet']):r for r in hg if r['method']=='C+AVG'}
    table(out/'human_paired_contrasts.csv',[{**r,'delta_minimum':r['minimum']-hbase[r['group_id'],r['sheet']]['minimum'] if r['minimum'] is not None and hbase[r['group_id'],r['sheet']]['minimum'] is not None else None,'delta_mean':r['mean']-hbase[r['group_id'],r['sheet']]['mean'] if r['mean'] is not None and hbase[r['group_id'],r['sheet']]['mean'] is not None else None} for r in hg])
    controls=read(out/'exact_controls.csv');maxgap=max(float(r['absolute_gap']) for r in controls)
    lookup={(r['assessor'],r['method']):r for r in summary}
    c=lookup['C_rank','C+Max-Min'];avg=lookup['C_rank','C+AVG'];gfar=lookup['C_rank','C+GFAR']
    a=lookup['A_rank','C+Max-Min'];ag=lookup['A_rank','C+GFAR'];aa=lookup['A_rank','C+AVG']
    frontier={assessor:[r['method'] for r in pareto if r['assessor']==assessor and r['nondominated']] for assessor in ['A_rank','C_rank']}
    human_selected=[r for r in hs if r['method'] in ['C+AVG','C+GFAR','C+Max-Min','C+Hybrid(0.50)']]
    selected_types=[r for r in summaries(primary,['assessor','method','type']) if r['assessor']=='C_rank' and r['method'] in ['C+AVG','C+GFAR','C+Max-Min','C+Hybrid(0.50)']]
    selected_masks=[r for r in masking if r['assessor']=='C_rank' and r['mask_percent'] in [0,75] and r['method'] in ['C+AVG','C+GFAR','C+Max-Min','C+Hybrid(0.50)']]
    text=f'''# TravelSync Experiment 4: Max-Min and Hybrid

## 1. Executive summary
Final recommendation: **Insufficient evidence for selection; collect independent group judgments.**
All 105 exact frozen groups were executed with both fixed assessors. Primary evaluator C-rank was frozen before results. Max-Min worst-member utility is {c['mean_minimum']:.6f}, compared with GFAR {gfar['mean_minimum']:.6f} and AVG {avg['mean_minimum']:.6f}. These are simulated rank utilities, not measured satisfaction.

## 2. Research questions
RQ1–4 examine worst-member gains, average-utility cost, lambda trade-offs and robustness by size, conflict and metadata missingness. RQ5 examines the limited historical human-label blocks. All lambda values were predefined; none was fitted to labels.

## 3. Algorithms and mathematical definitions
U_u(S)=sum_t r(u,i_t)/log2(t+1)/Z_u; Z_u is ideal topK discounted relevance over the full hard-eligible universe. Exact raw-score tied bands receive their expected triangular top100 weights using the unchanged Experiment 3 function. Greedy appends the candidate maximizing lambda*mean(U)+(1-lambda)*min(U), then mean, then stable ID; objective and mean rounded12 for ties. Prefix denominators never change. First step evaluates actual discounted candidate utilities. Members and candidates are sorted before arithmetic.
Optimization uses the frozen group recommendation C scores (including targeted sparse stress); evaluation uses unmasked A/C. Thus C-rank shares its source proxy with optimization, except stress perturbations, and is **not independent validation**. A-rank is an alternative fixed proxy, not human ground truth. Lambda0 equals Max-Min. Lambda1 sorts by mean normalized rank relevance; historical AVG sorts by mean full-universe percentile scores. They are mathematically different. Empty/zero-ideal groups are N/A with explicit status. Max-Min/Hybrid fill K when feasible; historical AWM intentionally retains its misery filter and may return shorter lists.

## 4. Reused data and experimental controls
Exact Experiment 3 group_profiles.json, individual_profiles.json, 105 group_scores NPZ files, shared A/C top100 candidate unions, full score matrices and nested description masks were reused by identifier. Source catalogue has 3570 rows; frozen eligible universe 3568 before group food exclusions. Sizes 2,3,4,6,10; group seeds11,29,47; seven types. Hard exclusions are inherited from frozen eligible IDs; no unsupported operating constraints inferred. Primary K10/L100/GFAR depth20. GFAR and historical AVG/AWM call the exact unchanged Experiment 3 implementation. See pre_execution_freeze.json for hashes, commit, dirty state, environment and dependency versions. No production integration or SRS/SDD change.

## 5. Objective correctness verification
Exact ordered permutation controls: 5 seeds × 5 lambdas, 7 candidates, 3 members, K3. Maximum observed greedy absolute gap {maxgap:.6f}. Exact enumeration proves the tiny optimum; greedy has no global optimality guarantee. Baseline regression checks compare every shared primary historical ranking and metric at tolerance1e-12. Tests and independent verification are recorded separately in tests.txt and final_validation.json. No claim is made for unexecuted tests.

## 6. Primary fairness–relevance results
{md([r for r in summary if r['assessor']=='C_rank'],['method','valid_groups','full_K_groups','mean_minimum','median_minimum','mean_member','mean_gap','mean_std','mean_jain'])}

## 7. Comparison against GFAR and AVG
Q1: Max-Min minus GFAR minimum-member utility under predefined C-rank = {c['mean_minimum']-gfar['mean_minimum']:+.6f}.
Q2: Max-Min minus AVG mean member utility = {c['mean_member']-avg['mean_member']:+.6f}; minus GFAR = {c['mean_member']-gfar['mean_member']:+.6f}. A positive difference is a gain, a negative difference is a sacrifice. Full group-level paired contrasts, wins/losses/ties, retention and Jaccard are in paired_contrasts.csv and contrast_summary.csv. AWM conditional/short-list results must not be interpreted as a fair full-K Pareto comparison.

## 8. Hybrid lambda sensitivity
Q3: Nondominated full-K macro-mean configurations under C-rank: {', '.join(frontier['C_rank'])}. Under A-rank: {', '.join(frontier['A_rank'])}. Macro-mean Pareto dominance does not imply dominance within every group. All five lambdas remain in tables, including duplicates and unfavorable outcomes. A Hybrid can improve both proxy means over greedy Max-Min because greedy Max-Min is not a global optimum; changing the greedy path can improve the final minimum.

![Fairness relevance and full-K Pareto frontier](figures/fairness_relevance_pareto.png)
![Lambda versus worst member](figures/lambda_minimum.png)
![Lambda versus mean member](figures/lambda_mean.png)

## 9. Results by group type and size
See by_type.csv, by_size.csv, by_type_and_size.csv and worst_groups.csv. These are constructed stress groups sharing twelve profiles, not independent samples of real users. Q4: Under A-rank, Max-Min minus GFAR worst-member utility={a['mean_minimum']-ag['mean_minimum']:+.6f}, minus AVG={a['mean_minimum']-aa['mean_minimum']:+.6f}. The small positive A-rank worst-member contrast agrees in direction with C-rank, but mean relevance decreases against GFAR and frontier membership changes. No universal lambda is established.

{md(selected_types,['type','method','mean_minimum','mean_member'])}

![Conflict types](figures/conflict_types.png)
![Group sizes](figures/group_sizes.png)
![Worst performing groups](figures/worst_groups.png)

## 10. Missing-description robustness
Executed all frozen masks (0/10/25/50/75%, seeds11/29/47/71/101), both native C and B-backbone/C-tie policies, for AVG/GFAR/Max-Min/all five hybrids. Candidate membership remains exact primary union, so candidate availability is constant within group. Frozen evaluators never change. Targeted sparse-group losses are combined with nested masks. masking_summary.csv reports utility, disparity, member support coverage and list retention. The inherited mask bundle supplies one common 0% reference and all five seeds at each positive percentage (21 frozen mask records); the common zero-mask reference is reused across seeds, not treated as independent evidence. Rank stability is not accuracy. Primary candidate diagnostics from Experiment 3 remain protected; L50/200 changed unions are separately labelled sensitivity, not primary evidence.

{md(selected_masks,['policy','mask_percent','method','mean_minimum','mean_member','mean_topk_retention'])}

![Missing descriptions](figures/missing_descriptions.png)

## 11. Restricted historical human-label comparison
Q5: The following actual values allow a restricted comparison with the simulations:
{md(human_selected,['method','sheet','groups','valid_groups','mean_minimum','mean_member'])}
Q5 interpretation: Max-Min's historical worst-member NDCG is higher than GFAR on both sheets, but its mean-member NDCG is lower, especially R2. Hybrid0.50 has lower worst-member NDCG than GFAR on R2 despite its strong C-rank result. Thus historical evidence is mixed: it weakly supports the Max-Min fairness direction and contradicts a universal simulation winner or cost-free human-relevance improvement.

All methods/lambdas and paired contrasts are in human_summary.csv and human_paired_contrasts.csv. Six fully judged historical blocks, synthetic mixed2/minority3/polarized4 groups, K=min(3,block size). Two sheets came from **one annotator**, not independent raters; members repeat profiles and groups share blocks. Labels predate enriched scoring metadata and were not re-adjudicated. Restricted-block rank proxy and ideal used to optimize; human exponential gains 2^grade-1 used only for assessment. Human DCG ideal zero or empty lists yield N/A, not zero. These data cannot validate 3568-POI group relevance. No lambda is selected from them.

![Restricted historical human judgments](figures/historical_human.png)

## 12. Runtime and scalability
{md(runtime_total,['method','aggregation_median_ms','aggregation_p95_ms','warm_offline_median_ms','warm_offline_p95_ms','relative_to_GFAR','relative_to_AVG'])}
Runtime includes trace construction, but excludes trace serialization. Warm offline sum combines newly measured restored-index member scoring, reconstructed candidate generation, normalization and aggregation; reusable stage timings are repeated across methods. It excludes model fitting, document preprocessing, evaluation, disk output, application/network and itinerary optimization. Masking uses cached projections; its timing is a saved-score offline diagnostic. runtime_summary.csv breaks down sizes/stages; raw records retain mask percentages and policies. No application latency claim.

![Runtime](figures/runtime.png)

## 13. Greedy approximation limitations
Greedy prefix optimization may lock in an early compromise and lose to an exact ordered list. Exact gap results above and exact_controls.csv quantify only tiny fixtures. Lambda1 has an additive mean objective and descending discounts, so sorting by normalized mean is optimal, up to documented rounded ties. No bound is claimed for lambda<1. Candidate decisions can be replayed from hashed frozen score matrices, pool IDs, configuration and selection traces; traces record winner, before/after utilities, discount, objective and tie counts plus a checksum of remaining candidate objective/mean arrays.

## 14. Threats to validity
Scorer circularity; limited independent human evidence; profile reuse and synthetic groups; incomplete/possibly stale metadata; explicit-only hard filters; candidate truncation; normalization and rank-proxy sensitivity; historical AVG/Hybrid1 mismatch; exact-tie assumptions; tiny exact fixtures; warm runtime reuse and noise; AWM length differences. L50/200, K5/20 and percentile/minmax optimization are diagnostics frozen before execution in config.json, fully reported in sensitivity_summary.csv. Raw A weighted scores and C cosine values are never mixed. No statistical independence or real-user satisfaction is asserted. Per-member output retains all utility distributions, not group means alone.

## 15. Final recommendation
Q6–7: **Insufficient evidence for selection; collect independent group judgments.** Keep this experiment isolated. Proxy results can guide further testing, but neither optimizing a proxy nor these restricted one-person historical labels justify a new production aggregator. Independent blinded group-level judgments on the enriched catalogue, including fairness preferences and hard-constraint checks, are needed before selecting a method for integration. No production adoption is recommended.
'''
    (out/'REPORT.md').write_text(text,encoding='utf-8')
    js(out/'decision.json',{'recommendation':'Insufficient evidence for selection; collect independent group judgments.','primary_assessor':'C_rank','maxmin_minus_gfar_minimum':c['mean_minimum']-gfar['mean_minimum'],'maxmin_minus_avg_mean':c['mean_member']-avg['mean_member'],'pareto':frontier})
