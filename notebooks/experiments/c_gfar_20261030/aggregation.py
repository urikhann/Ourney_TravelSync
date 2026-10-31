"""Offline AVG/AWM and traced GFAR, paper Eq.1/5 + SDD 9.2 boundaries/ties.

Raw scores determine member ranks only. Calibrated ordinal scores determine
AVG/AWM and the SDD mean-score tie breaker; they are not satisfaction ratings.
"""
import hashlib
import numpy as np


def validate(scores,ids,members,k):
    x=np.asarray(scores,dtype=float)
    if isinstance(k,bool) or not isinstance(k,int) or k<0:raise ValueError('Invalid K')
    if not members or len(set(members))!=len(members) or len(set(ids))!=len(ids):raise ValueError('Ambiguous identities')
    if x.shape!=(len(members),len(ids)) or not np.isfinite(x).all():raise ValueError('Missing, nonfinite or misaligned relevance')
    return x


def calibrate(scores,method='percentile'):
    """Tie-preserving dimensionless proxies, never empirical satisfaction fits."""
    x=np.asarray(scores,dtype=float)
    if x.ndim!=2 or not np.isfinite(x).all():raise ValueError('Invalid scores')
    y=np.zeros_like(x)
    for u,row in enumerate(x):
        if not len(row):continue
        if method=='minmax':
            span=np.ptp(row)
            y[u]=(row-row.min())/span if span>0 else 0
        elif method=='percentile':
            if len(row)==1:y[u]=.5;continue
            _,inverse,counts=np.unique(row,return_inverse=True,return_counts=True)
            starts=np.r_[0,np.cumsum(counts)[:-1]]
            y[u]=((starts+(counts-1)/2)/(len(row)-1))[inverse]
        else:raise ValueError('Unknown calibration')
    return y


def policy_backbone(b,c):
    """Ordinal encoding of B-primary, C-secondary exact tied bands."""
    pairs=list(zip(np.asarray(b).tolist(),np.asarray(c).tolist()))
    index={pair:i for i,pair in enumerate(sorted(set(pairs)))}
    return np.asarray([index[pair] for pair in pairs],dtype=float)


def borda(scores,ids,depth):
    if not isinstance(depth,int) or isinstance(depth,bool) or depth<1:raise ValueError('Invalid depth')
    x=np.asarray(scores,dtype=float);p=np.zeros_like(x)
    n=min(depth,len(ids))
    if not n:return p
    w=np.arange(n-1,-1,-1,dtype=float)
    w=w/w.sum() if n>1 else np.ones(1)
    for u,row in enumerate(x):
        order=sorted(range(len(ids)),key=lambda i:(-row[i],ids[i]))[:n]
        p[u,order]=w
    return p


def aggregate(raw,scaled,ids,members,algorithm='GFAR',k=10,depth=20,threshold=.5):
    raw=validate(raw,ids,members,k);scaled=validate(scaled,ids,members,k)
    if np.any((scaled<0)|(scaled>1)) or not np.isfinite(threshold):raise ValueError('Expected finite 0..1 proxy scores')
    # Sorting members fixes floating-point summation order under permutations.
    ui=sorted(range(len(members)),key=lambda i:members[i]);ci=sorted(range(len(ids)),key=lambda i:ids[i])
    users=[members[i] for i in ui];items=[ids[i] for i in ci]
    x=raw[np.ix_(ui,ci)];z=scaled[np.ix_(ui,ci)]
    means=z.mean(axis=0);n=len(items)
    if algorithm in ['AVG','AWM']:
        eligible=np.ones(n,dtype=bool) if algorithm=='AVG' else np.all(z>=threshold,axis=0)
        order=sorted((i for i in range(n) if eligible[i]),key=lambda i:(-means[i],items[i]))[:k]
        return [items[i] for i in order],[],{'awm_rejected':int((~eligible).sum()),'static_group_scores':{items[i]:float(means[i]) for i in range(n) if eligible[i]}}
    if algorithm!='GFAR':raise ValueError('Unknown aggregator')
    p=borda(x,items,depth);q=np.ones(len(users));available=np.ones(n,dtype=bool)
    selected=[];trace=[]
    personal=sorted(range(n),key=lambda i:(-x[0,i],items[i])) if len(users)==1 else None
    for step in range(min(k,n)):
        gains=q@p
        candidates=np.flatnonzero(available).tolist()
        best=max(round(float(gains[i]),12) for i in candidates)
        tied=[i for i in candidates if round(float(gains[i]),12)==best]
        chosen=personal[step] if personal is not None else min(tied,key=lambda i:(-means[i],items[i]))
        before=q.copy();after=q*(1-p[:,chosen]);contrib=before*p[:,chosen]
        tied_ids=sorted(items[i] for i in tied)
        trace.append({'position':step+1,'selected_poi_id':items[chosen],'member_ids':users,'member_relevance':p[:,chosen].tolist(),'member_gain_contributions':contrib.tolist(),'selection_gain':float(gains[chosen]),'unmet_before':before.tolist(),'unmet_after':after.tolist(),'satisfaction_before':(1-before).tolist(),'satisfaction_after':(1-after).tolist(),'gain_tie_count':len(tied),'gain_tie_id_sha256':hashlib.sha256('\n'.join(tied_ids).encode()).hexdigest(),'gain_tie_first5':tied_ids[:5],'selected_mean_proxy':float(means[chosen]),'tie_rule':'Single-member personal-order extension' if personal is not None else 'gain rounded12, mean calibrated proxy descending, stable ID ascending','depth_actual':min(depth,n)})
        selected.append(items[chosen]);available[chosen]=False;q=after
    return selected,trace,{'awm_rejected':0,'static_group_scores':{}}


def expected_rank_relevance(scores,support=100):
    """Expected triangular top-support utility under uniform exact-score ties.

    This is a scorer-derived simulation assumption, NOT independent relevance.
    Full tied bands (including those crossing support) share the mean weight.
    """
    x=np.asarray(scores,dtype=float)
    if x.ndim!=2 or not np.isfinite(x).all():raise ValueError('Invalid evaluation scores')
    result=np.zeros_like(x)
    for u,row in enumerate(x):
        start=1
        for value in sorted(set(row.tolist()),reverse=True):
            ids=np.flatnonzero(row==value);end=start+len(ids)-1;last=min(end,support)
            count=max(0,last-start+1)
            weight=(count*(support+1)-(start+last)*count/2)/support/len(ids) if count else 0
            result[u,ids]=weight;start=end+1
    return result


def utilities(relevance,columns,k=10):
    """Discounted expected-rank utility / fixed-K ideal, shorter lists penalized."""
    e=np.asarray(relevance,dtype=float)
    if e.ndim!=2 or not np.isfinite(e).all():raise ValueError('Invalid evaluator')
    if not len(columns):return [None]*len(e)
    discount=1/np.log2(np.arange(2,k+2))
    result=[]
    for row in e:
        ideal=float(np.sort(row)[::-1][:k]@discount[:min(k,len(row))])
        result.append(float(row[columns]@discount[:len(columns)])/ideal if ideal>0 else None)
    return result


def utility_metrics(values):
    if any(v is None for v in values) or not values:
        return {'minimum_utility':None,'mean_utility':None,'utility_gap':None,'utility_std':None,'jain':None,'utility_threshold_coverage':None}
    a=np.asarray(values,dtype=float);den=len(a)*float(a@a)
    return {'minimum_utility':float(a.min()),'mean_utility':float(a.mean()),'utility_gap':float(np.ptp(a)),'utility_std':float(a.std()),'jain':float(a.sum()**2/den) if den>0 else None,'utility_threshold_coverage':float((a>=.5).mean())}
