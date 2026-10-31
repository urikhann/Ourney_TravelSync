"""Standalone scientific figures from saved result tables; no refitting."""
import argparse,csv,os
from pathlib import Path
import statistics
os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).resolve().parent/'.plot-cache'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def read(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def plot(out):
    rows=read(out/'per_group_results.csv');summary=read(out/'primary_summary.csv')
    colors={'A+AVG':'#707070','A+AWM':'#a98540','A+GFAR':'#006fa6','C+AVG':'#3b9954','C+AWM':'#bb6f2b','C+GFAR':'#b33975'}
    fig,axes=plt.subplots(1,2,figsize=(11,4.6),sharex=True,sharey=True)
    for ax,assessor in zip(axes,['A_rank','C_rank']):
        for config,color in colors.items():
            scorer,agg=config.split('+');selected=[r for r in rows if r['stage']=='primary' and r['assessor']==assessor and r['scorer']==scorer and r['aggregator']==agg and r['mean_utility']]
            ax.scatter([float(r['mean_utility']) for r in selected],[float(r['minimum_utility']) for r in selected],color=color,s=10,alpha=.12)
            mean=next(r for r in summary if r['assessor']==assessor and r['scorer']==scorer and r['aggregator']==agg)
            if mean['mean_member_utility']:
                ax.scatter(float(mean['mean_member_utility']),float(mean['mean_minimum_utility']),color=color,s=85,edgecolor='white',linewidth=.8,label=config)
        ax.plot([0,1],[0,1],color='#cccccc',lw=.7);ax.set(xlim=(-.02,1.04),ylim=(-.02,1.04),xlabel='Mean member rank utility',title=f'Fixed {assessor} evaluator');ax.grid(alpha=.15)
    axes[0].set_ylabel('Worst-member rank utility');axes[1].legend(fontsize=8,loc='lower right')
    fig.suptitle('Fairness–relevance trade-off in controlled simulations',fontsize=12)
    fig.text(.5,.01,'Small points: constructed groups. Large points: macro means; empty AWM lists excluded from utility means.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.04,1,.94));fig.savefig(out/'fairness_relevance.png',dpi=180);fig.savefig(out/'fairness_relevance.svg');plt.close(fig)
    masks=read(out/'missing_description_summary.csv')
    fig,axes=plt.subplots(1,3,figsize=(13,4))
    for policy,style in [('C_native','-'),('B_backbone_C_ties','--')]:
        for agg,color in [('AVG','#3b9954'),('GFAR','#b33975')]:
            rs=[r for r in masks if r['policy']==policy and r['aggregator']==agg]
            rs.sort(key=lambda r:int(r['mask_percent']));x=[int(r['mask_percent']) for r in rs]
            for ax,field,title in zip(axes,['mean_minimum_utility','mean_utility','mean_topk_retention'],['Worst-member utility','Mean member utility','Group Top-10 retention']):
                ax.plot(x,[float(r[field]) if r[field] else float('nan') for r in rs],style,marker='o',ms=4,color=color,label=agg+' / '+('native C' if policy=='C_native' else 'B then C ties'))
                ax.set(title=title,xlabel='Additional description masking (%)',ylim=(-.02,1.04));ax.grid(alpha=.15)
    axes[2].legend(fontsize=7,loc='best');fig.suptitle('Fixed C-rank evaluator and fixed shared candidate unions',fontsize=11);fig.tight_layout(rect=(0,0,1,.92));fig.savefig(out/'description_robustness.png',dpi=180);fig.savefig(out/'description_robustness.svg');plt.close(fig)
    hs=read(out/'human_summary.csv')
    fig,ax=plt.subplots(figsize=(7.5,4.3));names=[r['scorer']+'+'+r['aggregator'] for r in hs]
    x=list(range(len(hs)))
    ax.bar([i-.18 for i in x],[float(r['mean_group_mean_human_ndcg']) for r in hs],width=.36,label='Mean-member historical NDCG',color='#4682a9')
    ax.bar([i+.18 for i in x],[float(r['mean_group_minimum_human_ndcg']) for r in hs],width=.36,label='Worst-member historical NDCG',color='#bb6f2b')
    ax.set(xticks=x,xticklabels=names,ylim=(0,1.05),ylabel='NDCG',title='Restricted human-labelled candidate blocks')
    ax.legend(fontsize=8);ax.grid(axis='y',alpha=.15);fig.text(.5,.01,'18 synthetic compositions × 2 sheets from one human; conditional means omit empty lists.',ha='center',fontsize=8);fig.tight_layout(rect=(0,.04,1,1));fig.savefig(out/'historical_human_comparison.png',dpi=180);fig.savefig(out/'historical_human_comparison.svg');plt.close(fig)
    print('Saved 3 PNG and 3 SVG figures to',out)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);a=p.parse_args();plot(a.out)
