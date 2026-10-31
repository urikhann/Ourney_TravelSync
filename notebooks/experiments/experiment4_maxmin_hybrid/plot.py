"""Publication-style static figures from saved Experiment 4 tables only."""
import argparse,csv,json,os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).parent/'.plot-cache'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def read(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def val(r,key):return float(r[key]) if r[key] else float('nan')
def plot(out):
    folder=out/'figures';folder.mkdir(exist_ok=True)
    primary=read(out/'primary_summary.csv');pareto=read(out/'pareto.csv')
    methods=[r['method'] for r in primary if r['assessor']=='C_rank']
    colors={m:plt.get_cmap('tab20')(i) for i,m in enumerate(methods)}
    def save(fig,name):
        fig.tight_layout();fig.savefig(folder/(name+'.png'),dpi=180,bbox_inches='tight');fig.savefig(folder/(name+'.svg'),bbox_inches='tight');plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13,5))
    for ax,assessor in zip(axes,['A_rank','C_rank']):
        rs=[r for r in primary if r['assessor']==assessor]
        for r in rs:ax.scatter(val(r,'mean_member'),val(r,'mean_minimum'),s=65,color=colors[r['method']],label=r['method'])
        frontier=[r for r in pareto if r['assessor']==assessor and r['nondominated']=='True'];frontier.sort(key=lambda r:val(r,'mean_member'))
        ax.plot([val(r,'mean_member') for r in frontier],[val(r,'mean_minimum') for r in frontier],':',color='black',alpha=.6)
        ax.set(title=assessor+' (fixed proxy)',xlabel='Mean member utility',ylabel='Mean worst-member utility');ax.grid(alpha=.2)
    axes[1].legend(fontsize=7,bbox_to_anchor=(1.02,1),loc='upper left');fig.suptitle('Fairness–relevance trade-off and full-length macro Pareto frontier')
    save(fig,'fairness_relevance_pareto')
    for metric,name,title in [('mean_minimum','lambda_minimum','Worst-member utility'),('mean_member','lambda_mean','Mean member utility')]:
        fig,ax=plt.subplots(figsize=(7,4))
        for assessor in ['A_rank','C_rank']:
            rs=sorted([r for r in primary if r['assessor']==assessor and 'Hybrid' in r['method']],key=lambda r:r['method'])
            ax.plot([float(r['method'].split('(')[1][:-1]) for r in rs],[val(r,metric) for r in rs],marker='o',label=assessor)
        ax.set(xlabel='Lambda (mean emphasis)',ylabel=title,title=title+' versus predefined lambda');ax.legend();ax.grid(alpha=.2);save(fig,name)
    for file,key,name in [('by_type.csv','type','conflict_types'),('by_size.csv','size','group_sizes')]:
        rs=read(out/file);fig,axes=plt.subplots(1,2,figsize=(13,5))
        for ax,metric,title in zip(axes,['mean_minimum','mean_member'],['Worst-member utility','Mean utility']):
            for m in ['C+AVG','C+GFAR','C+Max-Min','C+Hybrid(0.25)','C+Hybrid(0.50)','C+Hybrid(0.75)','C+Hybrid(1.00)']:
                subset=[r for r in rs if r['assessor']=='C_rank' and r['method']==m]
                if key=='size':subset.sort(key=lambda r:int(r[key]))
                ax.plot([r[key] for r in subset],[val(r,metric) for r in subset],marker='o',ms=3,label=m,color=colors[m])
            ax.set(title=title+' — fixed C-rank',ylabel=title);ax.tick_params(axis='x',rotation=35 if key=='type' else 0);ax.grid(alpha=.2)
        axes[1].legend(fontsize=7,bbox_to_anchor=(1.02,1));save(fig,name)
    rs=read(out/'masking_summary.csv');fig,axes=plt.subplots(2,3,figsize=(15,8))
    for row,policy in enumerate(['C_native','B_backbone_C_ties']):
        for ax,metric,title in zip(axes[row],['mean_minimum','mean_member','mean_topk_retention'],['Worst utility','Mean utility','TopK retention']):
            for m in ['C+AVG','C+GFAR','C+Max-Min','C+Hybrid(0.25)','C+Hybrid(0.50)','C+Hybrid(0.75)','C+Hybrid(1.00)']:
                subset=sorted([r for r in rs if r['assessor']=='C_rank' and r['policy']==policy and r['method']==m],key=lambda r:int(r['mask_percent']))
                ax.plot([int(r['mask_percent']) for r in subset],[val(r,metric) for r in subset],label=m,color=colors[m],marker='o',ms=3)
            ax.set(title=policy+' / '+title,xlabel='Description mask (%)');ax.grid(alpha=.2)
    axes[0,2].legend(fontsize=6);fig.suptitle('Fixed C-rank, fixed candidates; retention is stability, not accuracy');save(fig,'missing_descriptions')
    rs=read(out/'runtime_primary.csv');fig,ax=plt.subplots(figsize=(9,4))
    ax.bar([r['method'] for r in rs],[val(r,'aggregation_median_ms') for r in rs],color=[colors[r['method']] for r in rs]);ax.set(ylabel='Median aggregation ms',title='Warm offline aggregation, including trace construction');ax.tick_params(axis='x',rotation=35);save(fig,'runtime')
    rs=read(out/'worst_groups.csv')[:10];fig,ax=plt.subplots(figsize=(10,5));ax.barh([r['group_id'] for r in rs],[val(r,'delta_fairness') for r in rs],color='#af5252');ax.set(xlabel='Worst utility delta vs C+AVG',title='Max-Min: lowest paired C-rank fairness deltas');save(fig,'worst_groups')
    rs=read(out/'human_summary.csv');fig,axes=plt.subplots(1,2,figsize=(13,5))
    for ax,sheet in zip(axes,['R1','R2']):
        selected=[r for r in rs if r['sheet']==sheet];xx=np.arange(len(selected))
        ax.bar(xx-.18,[val(r,'mean_minimum') for r in selected],width=.36,label='Worst member');ax.bar(xx+.18,[val(r,'mean_member') for r in selected],width=.36,label='Mean member');ax.set_xticks(xx,[r['method'] for r in selected],rotation=65,ha='right',fontsize=7);ax.set(title=sheet+' — same one annotator',ylabel='Historical human NDCG');ax.legend(fontsize=7)
    save(fig,'historical_human')
    (out/'plot_environment.json').write_text(json.dumps({'matplotlib':matplotlib.__version__,'numpy':np.__version__,'figures':len(list(folder.glob('*.png')))},indent=2),encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);plot(p.parse_args().out)
