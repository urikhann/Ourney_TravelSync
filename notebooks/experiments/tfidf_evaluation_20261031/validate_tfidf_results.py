"""Independent exhaustive metric verification plus byte-identical clean repeat."""
import argparse
from collections import defaultdict
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path


def rows(path):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        return list(csv.DictReader(stream))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def oracle(group, k, threshold, expected):
    """Scalar permutations; no production metric/scoring functions imported."""
    grade={r['place_id']:int(r['grade']) for r in group}
    relevant={pid for pid,g in grade.items() if g>=threshold}
    ordered=sorted(group,key=lambda r:int(r['judged_pool_rank']))
    if expected:
        bands=[]
        for _,items in itertools.groupby(ordered,key=lambda r:float(r['score'])):
            bands.append(tuple(r['place_id'] for r in items))
        orders=(sum(parts,()) for parts in itertools.product(*(itertools.permutations(band) for band in bands)))
    else:
        orders=[tuple(r['place_id'] for r in ordered)]
    ideal=sum(g/math.log2(i+2) for i,g in enumerate(sorted([2**g-1 for g in grade.values()],reverse=True)[:k]))
    totals=defaultdict(float)
    count=0
    for order in orders:
        hits=sum(pid in relevant for pid in order[:k])
        dcg=sum((2**grade[pid]-1)/math.log2(i+2) for i,pid in enumerate(order[:k]))
        totals['ndcg']+=dcg/ideal if ideal else 0
        totals['precision']+=hits/k
        totals['recall']+=hits/len(relevant) if relevant else 0
        first=next((i for i,pid in enumerate(order,1) if pid in relevant),None)
        totals['mrr']+=1/first if first else 0
        count+=1
    result={name:value/count for name,value in totals.items()}
    if not ideal:result['ndcg']=None
    if not relevant:result.update(recall=None,mrr=None)
    return result


def validate(first, repeat, output):
    hashes=json.loads((first/'output_hashes.json').read_text(encoding='utf-8'))
    for name,expected in hashes.items():
        assert sha(first/name)==expected, f'Output hash mismatch: {name}'
    files=sorted(path.name for path in first.iterdir() if path.is_file())
    assert files==sorted(path.name for path in repeat.iterdir() if path.is_file())
    assert all(sha(first/name)==sha(repeat/name) for name in files), 'Repeat mismatch'
    judged=defaultdict(list)
    for row in rows(first/'judged_rankings.csv'):
        judged[(row['sheet'],row['scenario_id'],row['method'])].append(row)
    maximum_error=0.0
    metrics=rows(first/'per_scenario_metrics.csv')
    for row in metrics:
        group=judged[(row['sheet'],row['scenario_id'],row['method'])]
        expected=oracle(group,int(row['k']),int(row['relevance_threshold']),row['ties']=='expected')
        for name in ('ndcg','precision','recall','mrr'):
            actual=float(row[name]) if row[name] else None
            if actual is None or expected[name] is None:
                assert actual is expected[name]
            else:
                maximum_error=max(maximum_error,abs(actual-expected[name]))
                assert math.isclose(actual,expected[name],abs_tol=1e-12)
    rankings=rows(first/'rankings.csv')
    groups=defaultdict(list)
    for row in rankings:
        groups[(row['scenario_id'],row['method'])].append(row)
    assert len(groups)==36
    universe=None
    for group in groups.values():
        ids={r['place_id'] for r in group}
        assert len(group)==len(ids)==3570
        assert [int(r['rank']) for r in group]==list(range(1,3571))
        assert all(float(a['score'])>=float(b['score']) for a,b in zip(group,group[1:]))
        if universe is None:universe=ids
        else:assert universe==ids
    full=rows(first/'full_catalogue_metrics.csv')
    assert all(not row[name] for row in full for name in ('ndcg','recall','mrr'))
    assert all(not row['precision'] for row in full if int(row['judged_at_k'])<int(row['k']))
    for row in rows(first/'summary.csv'):
        selected=[r for r in metrics if r['sheet']==row['sheet'] and r['method']==row['method']
            and r['k']==row['k'] and r['ties']=='expected' and r['relevance_threshold']=='2'
            and (row['category']=='ALL' or r['category']==row['category'])]
        for name in ('ndcg','precision','recall','mrr'):
            values=[float(r[name]) for r in selected if r[name]]
            assert math.isclose(float(row[name]),sum(values)/len(values),abs_tol=1e-12)
    manifest=json.loads((first/'manifest.json').read_text(encoding='utf-8'))
    root=Path(__file__).resolve().parents[3]
    frozen=root/'data/generated/evaluation/enriched_poi_round1_20261028'
    frozen_manifest=json.loads((frozen/'manifest.json').read_text(encoding='utf-8'))
    for name,expected in frozen_manifest['frozen_artifacts'].items():
        assert sha(frozen/name)==expected,f'Frozen pre-scoring artifact changed: {name}'
    for source in frozen_manifest['inputs']:
        assert sha(frozen/source['frozen_path'])==source['sha256']
    old_run=json.loads((frozen/'results/run_manifest.json').read_text(encoding='utf-8'))
    for name in ('annotation_R1.csv','annotation_R2.csv'):
        assert sha(frozen/'results'/name)==old_run['output_sha256'][name]
    for name,expected in manifest['source_hashes'].items():
        assert sha(root/name)==expected,f'Protected source changed: {name}'
    for name,expected in manifest['human_source_hashes'].items():
        assert sha(root/name)==expected,f'Human source changed: {name}'
    result=dict(status='PASS',metric_rows_independently_verified=len(metrics),
        maximum_absolute_metric_error=maximum_error,rank_rows_checked=len(rankings),
        byte_identical_repeat_files=len(files),protected_sources_unchanged=True,
        unjudged_items_not_converted_to_zero=True,frozen_scenarios_and_annotation_packets_verified=True,
        method='Exhaustive within-tie permutations, scalar metric oracle')
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('first',type=Path)
    p.add_argument('repeat',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    validate(args.first,args.repeat,args.output)
