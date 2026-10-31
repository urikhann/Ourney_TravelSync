"""Add readable executed-result interpretation and exact command provenance."""
import argparse,csv,json,subprocess,sys
from pathlib import Path

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]


def read(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def summarize(out):
    primary=read(out/'primary_summary.csv');sensitivity=read(out/'sensitivity_summary.csv');human=read(out/'human_summary.csv')
    p={(r['scorer'],r['aggregator'],r['assessor']):r for r in primary};h={(r['scorer'],r['aggregator']):r for r in human}
    cv,cg,ca=[p[('C',a,'C_rank')] for a in ['AVG','GFAR','AWM']]
    f=lambda r,k:float(r[k])
    worst_pct=100*(f(cg,'mean_minimum_utility')/f(cv,'mean_minimum_utility')-1)
    mean_pct=100*(f(cg,'mean_member_utility')/f(cv,'mean_member_utility')-1)
    mm={r['aggregator']:r for r in sensitivity if r['stage']=='normalization_sensitivity' and r['scorer']=='C' and r['assessor']=='C_rank' and r['calibration']=='minmax' and r['aggregator'] in ['AVG','GFAR']}
    mm_pct=100*(f(mm['GFAR'],'mean_minimum')/f(mm['AVG'],'mean_minimum')-1)
    block=f'''## Executed-result interpretation

**The default C+GFAR condition did not improve worst-member utility over C+AVG under the fixed C-rank assessor.** Mean worst-member utility changed from {f(cv,'mean_minimum_utility'):.6f} to {f(cg,'mean_minimum_utility'):.6f} ({worst_pct:+.2f}%), while mean member utility changed from {f(cv,'mean_member_utility'):.6f} to {f(cg,'mean_member_utility'):.6f} ({mean_pct:+.2f}%). Of 105 paired groups, 28 improved, 15 tied and 62 worsened on minimum utility. Mean disparity decreased slightly and Jain increased, showing that fairness metrics need not agree.

C+GFAR does improve mean worst-member utility relative to C+AWM ({f(ca,'mean_minimum_utility'):.6f} → {f(cg,'mean_minimum_utility'):.6f}), but C+AWM returns a full ten items in only 66/105 groups. C+GFAR returns ten in all 105. A+AWM is empty in 50/105 groups. Comparing conditional means without those coverage differences would be misleading.

**Calibration changes the conclusion.** Under min–max proxies, C+GFAR raises mean worst-member C-rank utility from {f(mm['AVG'],'mean_minimum'):.6f} to {f(mm['GFAR'],'mean_minimum'):.6f} ({mm_pct:+.2f}%). Under percentile proxies it lowers it. Under the alternative fixed A-rank evaluator it also improves over C+AVG. Neither proxy is validated as satisfaction, so these differences do not justify selecting whichever normalization gives the desired winner. GFAR's summed-coverage objective does not directly maximize the reported minimum-member metric.

The restricted historical-label assessment is more favourable: C+GFAR has mean worst-member NDCG {f(h[('C','GFAR')],'mean_group_minimum_human_ndcg'):.6f}, compared with {f(h[('C','AVG')],'mean_group_minimum_human_ndcg'):.6f} for C+AVG and {f(h[('A','GFAR')],'mean_group_minimum_human_ndcg'):.6f} for A+GFAR. Mean-member NDCG is {f(h[('C','GFAR')],'mean_group_mean_human_ndcg'):.6f} versus {f(h[('C','AVG')],'mean_group_mean_human_ndcg'):.6f} for C+AVG. These are 18 constructed compositions in six small labelled blocks, two sheets from one person, and revised scoring text; they are not independent group-satisfaction observations.

Under the C-rank assessor, the default GFAR-versus-AVG minimum-utility contrast is best for polarized groups (+0.0340) and the targeted sparse-metadata stress (+0.0292), and worst for strong disagreement (−0.1323) and minority groups (−0.0802). Two-member groups gain +0.0351; the larger sizes have negative mean minimum-utility changes. These are descriptive strata, not significance claims.

At 75% description masking, native C+GFAR's mean minimum utility is 0.4049 versus 0.3618 for native C+AVG, reversing the unmasked default contrast. Native GFAR retains 64.65% of its original group Top-10, while the B-backbone/C-tie GFAR retains 89.18%. The more stable fallback has *lower* mean minimum utility (0.3844), illustrating that stability is not relevance accuracy. Recomputed candidate-union changes and individual-rank stability are separately measured in mask_candidate_diagnostics.csv; primary mask comparisons keep candidate IDs fixed.

Depth100, kept as a diagnostic rather than a post-hoc replacement for the frozen depth20, gives C+GFAR mean minimum/mean utility 0.5071/0.6738 under C-rank. This sensitivity motivates prospective validation, not a claim that a tuned architecture has been independently confirmed.

**Final recommendation: Insufficient evidence; more evaluation required.** The frozen readiness rule fails on the worst-member gain criterion. Keep the application-facing algorithm unchanged. C+GFAR remains a useful controlled comparator, but the results do not support a general fairness improvement or a production replacement.

## Exported figures

![Fairness versus simulated relevance under two fixed evaluators](fairness_relevance.png)

![Missing-description utility and ranking stability](description_robustness.png)

![Restricted historical human-label comparison](historical_human_comparison.png)

Standalone SVG versions of all figures are included. Figures were visually inspected; empty-list and simulation limits are stated in captions.

'''
    report=out/'REPORT.md';text=report.read_text(encoding='utf-8')
    if '## Executed-result interpretation\n' not in text:text=text.replace('## GFAR definition and recovered references\n',block+'## GFAR definition and recovered references\n',1)
    report.write_text(text,encoding='utf-8')
    semantic=ROOT/'notebooks/experiments/enriched_poi_round1_20261028/.semantic-venv/Scripts/python.exe'
    plot=HERE/'.plot-venv/Scripts/python.exe'
    commands={'scoring_and_evaluation':[str(semantic),str(HERE/'experiment.py'),'--out',str(out)],'repeat':[str(semantic),str(HERE/'experiment.py'),'--out',str(out.parent/'c_gfar_20261030_repeat01')],'plots':[str(plot),str(HERE/'plot_results.py'),'--out',str(out)],'validation':[str(semantic),str(HERE/'verify_results.py'),'--out',str(out)],'interpretation_export':[str(semantic),str(Path(__file__).resolve()),'--out',str(out)]}
    (out/'commands.json').write_text(json.dumps(commands,indent=2),encoding='utf-8')
    environment=subprocess.check_output([str(plot),'-c',"import json,sys,importlib.metadata; print(json.dumps({'python':sys.version,'matplotlib':importlib.metadata.version('matplotlib'),'numpy':importlib.metadata.version('numpy'),'pillow':importlib.metadata.version('pillow')}))"],text=True)
    (out/'plot_environment.json').write_text(environment,encoding='utf-8')
    print('Added result interpretation, figure links and exact commands to',out)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();summarize(a.out.resolve())
