"""Deterministic fixed-denominator ordered-list proxy optimization, version 1."""
from itertools import permutations
import hashlib
import numpy as np


def discounts(k):
    if isinstance(k, bool) or not isinstance(k, int) or k < 0:
        raise ValueError('K must be a nonnegative integer')
    return 1 / np.log2(np.arange(2, k + 2))


def prepare(relevance, ids, members, k, normalizers, excluded=()):
    d = discounts(k)
    x = np.asarray(relevance, dtype=float)
    z = np.asarray(normalizers, dtype=float)
    if not members or len(set(members)) != len(members) or len(set(ids)) != len(ids):
        raise ValueError('Unique identities and at least one member required')
    if x.shape != (len(members), len(ids)) or not np.isfinite(x).all() or (x < 0).any():
        raise ValueError('Relevance must be finite, nonnegative and aligned')
    if z.shape != (len(members),) or not np.isfinite(z).all() or (z < 0).any():
        raise ValueError('Invalid full-universe normalizers')
    ui = sorted(range(len(members)), key=lambda u: members[u])
    ci = sorted((i for i in range(len(ids)) if ids[i] not in set(excluded)), key=lambda i: ids[i])
    items = [ids[i] for i in ci]
    users = [members[u] for u in ui]
    if (z == 0).any():
        return None, items, users, d
    return x[np.ix_(ui, ci)] / z[ui, None], items, users, d


def objective(values, lam):
    if not np.isfinite(lam) or not 0 <= lam <= 1:
        raise ValueError('Lambda outside [0,1]')
    a = np.asarray(values, dtype=float)
    return lam * a.mean(axis=0) + (1 - lam) * a.min(axis=0)


def greedy(relevance, ids, members, k, normalizers, lam=0., excluded=(), trace=True):
    """Round objective/mean to 12 decimals for ties, then ascending stable ID.

    Zero ideal utility makes the whole group objective undefined: no ranking,
    explicit UNDEFINED_NORMALIZER, never an invented zero utility.
    """
    objective(np.zeros(len(members)), lam)
    x, items, users, d = prepare(relevance, ids, members, k, normalizers, excluded)
    if x is None:
        return [], [], {'status': 'UNDEFINED_NORMALIZER', 'shortage': k}
    available = np.ones(len(items), dtype=bool)
    current = np.zeros(len(users)); selected = []; decisions = []
    for t in range(min(k, len(items))):
        hypothetical = current[:, None] + d[t] * x
        scores = objective(hypothetical, lam); means = hypothetical.mean(axis=0)
        candidates = np.flatnonzero(available)
        best = np.max(np.round(scores[candidates], 12))
        tied = candidates[np.round(scores[candidates], 12) == best]
        second = np.max(np.round(means[tied], 12))
        finalists = tied[np.round(means[tied], 12) == second]
        chosen = int(finalists[0])
        if trace:
            decisions.append({'position': t+1, 'selected_poi_id': items[chosen],
                'member_ids': users, 'member_utilities_before': current.tolist(),
                'member_utilities_after': hypothetical[:, chosen].tolist(),
                'discount': float(d[t]), 'objective': float(scores[chosen]),
                'mean_utility': float(means[chosen]), 'minimum_utility': float(hypothetical[:, chosen].min()),
                'objective_tie_count': len(tied), 'final_tie_count': len(finalists),
                'remaining_candidates': len(candidates),
                'decision_sha256': hashlib.sha256(np.round(np.vstack([scores[candidates], means[candidates]]),12).tobytes()).hexdigest(),
                'tie_policy': 'objective rounded12, mean rounded12, stable ID ascending'})
        current = hypothetical[:, chosen]; selected.append(items[chosen]); available[chosen] = False
    return selected, decisions, {'status': 'OK' if len(selected)==k else 'CANDIDATE_SHORTAGE', 'shortage': max(0,k-len(selected)), 'objective': float(objective(current,lam)), 'member_utilities': current.tolist()}


def exact(relevance, ids, members, k, normalizers, lam=0., excluded=()):
    """Enumerate ordered permutations for tiny fixtures only (n<=9, K<=5)."""
    objective(np.zeros(len(members)), lam)
    x, items, users, d = prepare(relevance, ids, members, k, normalizers, excluded)
    if x is None:
        return [], None
    if len(items)>9 or k>5:
        raise ValueError('Exact reference restricted to tiny problems')
    best = None; order = None
    for p in permutations(range(len(items)), min(k,len(items))):
        values = x[:,p] @ d[:len(p)]
        key = (round(float(objective(values,lam)),12), round(float(values.mean()),12))
        if best is None or key>best:
            best=key; order=p; result=float(objective(values,lam))
    return [items[i] for i in order], result


def ideal(relevance, k):
    a = np.asarray(relevance,dtype=float)
    d = discounts(k)
    return np.sort(a,axis=1)[:,::-1][:,:k] @ d[:min(k,a.shape[1])]
