"""Evaluate externally supplied, complete, held-out human judgments.

No label generation, parameter search, imputation, or calibration. Input score
columns: participant_id, place_id, method, score. Labels use the packet schema.
Only ranks on the supplied fully judged pool are evaluated, not catalogue recall.
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr

def metrics(scores, grades, willingness, k):
    scores, grades = np.asarray(scores), np.asarray(grades)
    order=np.argsort(-scores,kind='stable')[:k]
    gains=2.**grades-1
    # Expected gain within exact ties avoids arbitrary ID tie advantages.
    expected=np.array([gains[scores==s].mean() for s in scores])
    dcg=sum(expected[i]/np.log2(j+2) for j,i in enumerate(order))
    ideal=sum(g/np.log2(j+2) for j,g in enumerate(sorted(gains,reverse=True)[:k]))
    expected_visit=np.array([np.asarray(willingness)[scores==s].mean() for s in scores])
    return {'ndcg':float(dcg/ideal) if ideal else None,
            'willingness_at_k':float(expected_visit[order].mean()),
            'spearman':float(spearmanr(scores,grades).statistic) if np.ptp(scores)>0 and np.ptp(grades)>0 else None,
            'judged_pool':len(scores),'k':k}

def read(path):
    with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def main():
    p=argparse.ArgumentParser();p.add_argument('--labels',required=True);p.add_argument('--scores',required=True)
    p.add_argument('--collection-record',required=True);p.add_argument('--out',required=True);args=p.parse_args()
    record=json.loads(Path(args.collection_record).read_text(encoding='utf-8'))
    for key in ['collection_date','collector','blinding_procedure','participant_group_split','frozen_method_configuration']:
        if not record.get(key):raise ValueError('Missing actual collection record: '+key)
    labels=read(args.labels);scores=read(args.scores)
    # Reject observable split leakage, while retaining the actual collection record.
    for field in ['participant_id','group_id','place_id']:
        seen=defaultdict(set)
        for r in labels:
            if not r.get(field) or r['split'] not in ['development','heldout']:
                raise ValueError('Missing identity or invalid split')
            seen[r[field]].add(r['split'])
        if any(len(s)>1 for s in seen.values()):
            raise ValueError('Development/heldout leakage: '+field)
    lookup={}
    for r in labels:
        if r['split']!='heldout':continue
        key=(r['participant_id'],r['place_id'])
        if not key[0] or key in lookup:raise ValueError('Missing participant or duplicate judgment')
        if r['insufficient_evidence_yes_no'].lower() not in ['yes','no']:raise ValueError('Missing evidence flag')
        if r['insufficient_evidence_yes_no'].lower()=='yes':
            if r['relevance_1_5'] or r['willingness_to_visit_yes_no']:raise ValueError('Insufficient evidence must remain unscored')
            lookup[key]=None;continue
        grade=int(r['relevance_1_5']);visit=r['willingness_to_visit_yes_no'].lower()
        if grade not in range(1,6) or visit not in ['yes','no']:raise ValueError('Invalid human judgment')
        lookup[key]=(grade,visit=='yes')
    batches=defaultdict(dict)
    for r in scores:
        key=(r['participant_id'],r['place_id'])
        if key not in lookup:raise ValueError('Unexpected or non-heldout prediction')
        batch=batches[(r['participant_id'],r['method'])]
        if key in batch:raise ValueError('Duplicate prediction')
        value=float(r['score'])
        if not np.isfinite(value):raise ValueError('Nonfinite prediction')
        batch[key]=value
    results=[]
    for (participant,method),batch in batches.items():
        expected={key for key in lookup if key[0]==participant}
        if batch.keys()!=expected:raise ValueError('Incomplete judged-pool predictions')
        if any(lookup[key] is None for key in expected):
            results.append(dict(participant=participant,method=method,status='EXCLUDED_INSUFFICIENT_EVIDENCE'));continue
        keys=sorted(expected)
        if len(keys)<10:raise ValueError('At least ten fully judged POIs required for @10')
        for k in [5,10]:
            results.append(dict(participant=participant,method=method,status='HELDOUT',**metrics([batch[key] for key in keys],[lookup[key][0] for key in keys],[lookup[key][1] for key in keys],k)))
    if not results:raise ValueError('No heldout human evidence')
    Path(args.out).write_text(json.dumps(results,indent=2),encoding='utf-8')

if __name__=='__main__':main()
