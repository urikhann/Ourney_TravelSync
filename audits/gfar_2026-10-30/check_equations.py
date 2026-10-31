"""Read-only GFAR audit; run from repo root with .venv/Scripts/python.exe.

Checks greedy choices against direct set-function differences, independently
of the incremental uncovered recurrence. Does not run Java or change inputs.
"""
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'notebooks/experiments/c_gfar_20261030'))
from ourney_travelsync import GFAR, GroupInput
from aggregation import aggregate, calibrate


def data(x):
    m, n = x.shape
    return GroupInput(tuple(f'u{i}' for i in range(m)),
                      tuple(f'p{i:04}' for i in range(n)),
                      np.full((m, 10), 3.),
                      np.tile([1.] + [0.] * 9, (n, 1)), x)


def probabilities(x, depth):
    m, n = x.shape
    length = min(depth, n)
    p = np.zeros((m, n))
    for u in range(m):
        order = sorted(range(n), key=lambda i: (-x[u, i], i))[:length]
        denominator = sum(length - rank for rank in range(1, length + 1))
        for rank, i in enumerate(order, 1):
            p[u, i] = (length - rank) / denominator
    return p


def objective(p, selected):
    return float(sum(1 - np.prod([1 - p[u, i] for i in selected])
                     for u in range(len(p))))


def check(x, depth, k):
    g = data(x)
    p = probabilities(x, depth)
    variants = [
        ('production', [(g.poi_ids.index(r.poi_id), r.score)
                        for r in GFAR(depth).recommend(g, top_k=k)]),
    ]
    picked, trace, _ = aggregate(x, calibrate(x), list(g.poi_ids),
                                list(g.user_ids), k=k, depth=depth)
    variants.append(('experiment', [(g.poi_ids.index(pid), t['selection_gain'])
                                    for pid, t in zip(picked, trace)]))
    count = 0
    for name, sequence in variants:
        selected = []
        for chosen, gain in sequence:
            before = objective(p, selected)
            available = [i for i in range(x.shape[1]) if i not in selected]
            gains = {i: objective(p, selected + [i]) - before for i in available}
            assert chosen in available, name
            # Experiment rounds gains to 12 decimals before breaking ties.
            assert max(gains.values()) - gains[chosen] < 1.1e-12, name
            assert abs(gain - gains[chosen]) < 1e-12, name
            selected.append(chosen)
            count += 1
    return count


def main():
    protected = [ROOT / 'src/ourney_travelsync/recommenders/gfar.py',
                 ROOT / 'src/ourney_travelsync/data/travelsync_final_places_v2.csv',
                 ROOT / 'notebooks/experiments/c_gfar_20261030/aggregation.py']
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in protected}
    rng = np.random.default_rng(20261030)
    steps = 0
    for trial in range(300):
        m, n = int(rng.integers(2, 9)), int(rng.integers(2, 26))
        x = (rng.integers(1, 6, (m, n)).astype(float) if trial % 2
             else rng.uniform(1, 5, (m, n)))
        steps += check(x, int(rng.integers(2, 31)), int(rng.integers(0, n + 3)))
    paper = np.array([[5, 4.5, 4, 4, .5], [5, 4.5, 4, 1.5, 3],
                      [2.5, 2.5, 3, 5, 1.]])
    results = GFAR(3).recommend(data(paper), top_k=3)
    assert [r.poi_id for r in results] == ['p0000', 'p0003', 'p0001']
    np.testing.assert_allclose([r.score for r in results], [4/3, 2/3, 2/9])
    minority = []
    x = np.array([[5.] * 100 + [1.] * 100] * 3 + [[1.] * 100 + [5.] * 100])
    for depth in [10, 20, 50, 100]:
        g = data(x)
        recs = GFAR(depth).recommend(g, top_k=10)
        columns = [g.poi_ids.index(r.poi_id) for r in recs]
        minority.append(dict(depth=depth, minority_picks=sum(i >= 100 for i in columns),
                             member_means=x[:, columns].mean(1).tolist()))
    source = ROOT / 'data/generated/evaluation/c_gfar_20261030_run01/per_group_results.csv'
    with source.open(encoding='utf-8', newline='') as f:
        rows = [r for r in csv.DictReader(f) if r['stage'] == 'primary'
                and r['scorer'] == 'C' and r['assessor'] == 'C_rank']
    summaries = {}
    paired = {}
    for alg in ['AVG', 'GFAR']:
        r = [r for r in rows if r['aggregator'] == alg]
        assert len(r) == 105
        summaries[alg] = {key: float(np.mean([float(a[key]) for a in r]))
                          for key in ['minimum_utility', 'mean_utility', 'utility_gap', 'jain']}
        paired[alg] = {a['group_id']: float(a['minimum_utility']) for a in r}
    delta = np.array([paired['GFAR'][g] - paired['AVG'][g] for g in paired['AVG']])
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == hashes[str(p.relative_to(ROOT))]
               for p in protected)
    print(json.dumps(dict(seed=20261030, random_fixtures=300,
                          verified_selection_steps=steps,
                          paper_example_ids=[r.poi_id for r in results],
                          paper_example_gains=[r.score for r in results],
                          minority_fixture=minority, saved_C_rank_primary=summaries,
                          saved_C_rank_pairs=dict(better=int(sum(delta > 1e-12)),
                                                  tied=int(sum(abs(delta) <= 1e-12)),
                                                  worse=int(sum(delta < -1e-12))),
                          protected_hashes_unchanged=hashes), indent=2))


if __name__ == '__main__':
    main()
