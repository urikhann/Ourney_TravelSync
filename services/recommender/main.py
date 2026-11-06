"""TravelSync recommender service: the team's AWM / GFAR package behind the HTTP contract.

    POST /recommend   contracts/recommend.request.json  ->  contracts/recommend.response.json
    GET  /health

This file only translates. All scoring and group aggregation is done by the `ourney_travelsync`
package (src/ourney_travelsync, owned by the recommender teammate):

    ratings (1-5) --E5 hybrid / feature fallback--> member x place relevance
                  --prepare_group------> candidates = union of each member's top-L (seeded ties)
                  --GFAR / AWM---------> group list

The response declares score semantics. GFAR gains and itinerary utility are separate.
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from importlib.metadata import version as pkg_version

import numpy as np
from fastapi import FastAPI, HTTPException

from ourney_travelsync import FEATURE_NAMES, load_places
from ourney_travelsync.places import PlaceDataset
from ourney_travelsync.features import validate_features
from ourney_travelsync.recommenders import prepare_group
from ourney_travelsync.recommenders.average_without_misery import AverageWithoutMisery
from ourney_travelsync.recommenders.gfar import GFAR
from ourney_travelsync.recommenders.ties import ranking
from ourney_travelsync.recommenders.tfidf import taste_query
from ourney_travelsync.recommenders.hybrid import ScoringConfig

FEATURE_SCHEMA_VERSION = "fs-1.0"
# The dataset version name the backend uses for the packaged enriched CSV (api/app/config.py PLACES_VERSION).
DATASET_VERSION = os.getenv("DATASET_VERSION", "busan-enriched-v3")
PLACES_CSV = os.getenv("PLACES_CSV") or None          # None = the CSV inside the package
GFAR_DEPTH = int(os.getenv("GFAR_DEPTH", "20"))         # Borda depth of each member's list (package default)
AWM_THRESHOLD = float(os.getenv("AWM_THRESHOLD", "2.5"))  # package default, 1-5 scale
MODEL_VERSION = f"ourney-travelsync-{pkg_version('ourney-travelsync')}"

LOVE = {"food": "love local food", "cafe": "love cafés", "nature": "love nature & views", "history": "love history",
        "art": "love art & shows", "shopping": "love shopping", "activity": "love activities",
        "entertainment": "love fun & entertainment", "relax": "love rest & walks", "festival": "love festivals"}

app = FastAPI(title="TravelSync recommender", version=MODEL_VERSION)


@lru_cache(maxsize=1)
def places():
    """Load the place data once (3,570 rows); later requests reuse it."""
    return load_places(PLACES_CSV) if PLACES_CSV else load_places()


def trip_seed(trip_id: str) -> int:
    """Ties are broken by a seeded hash. A seed per trip gives each group its own (repeatable) order
    inside a tied category, instead of every group getting the same lucky places with seed 0."""
    return int(hashlib.sha256(trip_id.encode()).hexdigest()[:8], 16)


def utility(rank: int, k: int) -> int:
    """SDD 9.3: group_utility = round(1000 * (K' - rank + 1) / K')."""
    return round(1000 * (k - rank + 1) / k)


def borda(scores: np.ndarray, depth: int) -> np.ndarray:
    """The same normalized Borda relevance GFAR uses internally (only for explanations here)."""
    n_users, n_pois = scores.shape
    rel = np.zeros_like(scores, dtype=float)
    length = min(depth, n_pois)
    if length < 2:
        return rel
    weights = np.arange(length - 1, -1, -1, dtype=float)
    weights /= weights.sum()
    for u in range(n_users):
        rel[u, np.argsort(-scores[u], kind="stable")[:length]] = weights
    return rel


def reasons(features: dict[str, int], members: list[dict], picked_for: str | None) -> list[dict]:
    out = []
    active = [f for f in FEATURE_NAMES if features.get(f)]
    if active:
        best = max(active, key=lambda f: (sum(m["ratings"][f] for m in members), -FEATURE_NAMES.index(f)))
        n = sum(1 for m in members if m["ratings"][best] >= 4)
        out.append({"code": "shared_interest",
                    "text": f"{n} of {len(members)} {LOVE[best]}" if n >= 2 else f"Matches {best}"})
    if picked_for and len(members) > 1:
        out.append({"code": "fair_pick", "text": f"Fair pick for {picked_for}", "user_id": picked_for})
    return out


def check(req: dict) -> None:
    if req.get("feature_schema_version") not in (None, FEATURE_SCHEMA_VERSION):
        raise HTTPException(422, "feature_schema_version_mismatch")
    if list(req.get("features") or FEATURE_NAMES) != list(FEATURE_NAMES):
        raise HTTPException(422, "feature_order_mismatch")
    if req.get("dataset_version") != DATASET_VERSION and req.get('poi_snapshot') is None:
        # The backend pinned a dataset this service does not have (SRS 7: versions must match).
        raise HTTPException(409, f"dataset_version_mismatch: service has {DATASET_VERSION}")
    if not req.get("members"):
        raise HTTPException(422, "no_members")
    if (req.get('options') or {}).get('individual_method', 'e5_hybrid') not in ('binary', 'tfidf', 'feature_mean', 'e5_hybrid'):
        raise HTTPException(422, 'unknown_individual_method')
    try:
        for member in req['members']:
            taste_query(member.get('tastes'))
    except ValueError:
        raise HTTPException(422, 'invalid_tastes') from None
    for m in req["members"]:
        r = m.get("ratings") or {}
        if set(r) != set(FEATURE_NAMES) or any(type(v) is not int or not 1 <= v <= 5 for v in r.values()):
            raise HTTPException(422, f"invalid_ratings: {m.get('user_id')}")
    if len({m['user_id'] for m in req['members']}) != len(req['members']):
        raise HTTPException(422, 'duplicate_members')


@app.post("/recommend")
def recommend(req: dict):
    check(req)
    members, opts = req["members"], req.get("options") or {}
    for field in ('top_l', 'group_k'):
        if field in opts and (type(opts[field]) is not int or opts[field] < 1):
            raise HTTPException(422, 'invalid_candidate_limit')
    if opts.get('tie_seed') is not None and type(opts['tie_seed']) is not int:
        raise HTTPException(422, 'invalid_tie_seed')
    method = opts.get("method", "gfar")
    if method not in ("gfar", "average", "awm"):
        raise HTTPException(422, "unknown_method")
    top_l, group_k = int(opts.get("top_l") or 100), int(opts.get("group_k") or 30)
    seed = int(opts["tie_seed"]) if opts.get("tie_seed") is not None else trip_seed(str(req.get("trip_id", "")))
    requested_method = opts.get('individual_method', 'e5_hybrid')
    individual_method = requested_method
    if method == 'awm':
        if requested_method == 'tfidf':
            raise HTTPException(422, 'awm_requires_feature_mean')
        individual_method = 'feature_mean'  # Explicit documented comparison route.
        threshold = opts.get('awm_threshold', None)
        if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                                      or not np.isfinite(threshold) or not 1 <= threshold <= 5):
            raise HTTPException(422, 'awm_threshold_must_be_between_1_and_5')
    if method == 'average' and requested_method == 'e5_hybrid':
        individual_method = 'feature_mean'  # Means of member percentiles are not rating proxies.
    scale = ('relative_relevance_0_1' if individual_method == 'e5_hybrid'
             else '0-1' if individual_method == 'tfidf' else '1-5')
    try:
        config = ScoringConfig.from_env(**{k: opts[k] for k in ('alpha', 'beta') if k in opts})
    except (TypeError, ValueError):
        raise HTTPException(422, 'invalid_scoring_configuration') from None
    if req.get('poi_snapshot') is not None:
        try:
            records = tuple(req['poi_snapshot'])
            ids = tuple(r['place_id'] for r in records)
            features = np.asarray([[r[f] for f in FEATURE_NAMES] for r in records], dtype=float).reshape(-1, 10)
            coordinates = np.asarray([[r['latitude'], r['longitude']] for r in records], dtype=float).reshape(-1, 2)
            validate_features(features, preferences=False)
            if len(ids) != len(set(ids)) or any(not isinstance(pid, str) or not pid.strip() for pid in ids):
                raise ValueError('invalid_ids')
            if any(r.get('dataset_version') != req['dataset_version'] for r in records):
                raise ValueError('dataset_version_mismatch')
            if not np.isfinite(coordinates).all() or np.any(np.abs(coordinates) > [90, 180]):
                raise ValueError('invalid_coordinates')
            P = PlaceDataset(records, ids, features, coordinates)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, 'invalid_poi_snapshot') from None
    else:
        P = places()
    known = set(P.place_ids)

    # E1: personal exclusions. Any member's rule removes the place for the whole group.
    # "resolved" = places the backend matched to a member's tag rule (e.g. hiking), with the tag as reason.
    removed, excluded = [], {}
    for m in members:
        ex = m.get("exclusions") or {}
        why = {**{pid: "place" for pid in ex.get("poi_ids", [])}, **(ex.get("resolved") or {})}
        for pid, reason in why.items():
            if pid in known and pid not in excluded:
                excluded[pid] = f"{reason} ({m['user_id']})"
    removed += [{"poi_id": pid, "stage": "E1", "rule": "member_exclusion", "detail": d} for pid, d in excluded.items()]

    users = {m["user_id"]: m["ratings"] for m in members}
    try:
        data = prepare_group(P, users, individual_top_n=top_l, tie_seed=seed, excluded_place_ids=excluded,
                             individual_method=individual_method,
                             scoring_config=config,
                             preference_texts={m['user_id']: taste_query(m.get('tastes')) for m in members})
    except ValueError as e:
        raise HTTPException(422, f"invalid_input: {e}")
    S = data.individual_scores  # [members, candidates], on the declared score scale
    col = {pid: j for j, pid in enumerate(data.poi_ids)}
    uids = list(data.user_ids)

    if method == "gfar":
        picks = GFAR(individual_top_n=GFAR_DEPTH).recommend(data, top_k=group_k)
    elif method == "awm":
        threshold = float(opts["awm_threshold"]) if opts.get("awm_threshold") is not None else AWM_THRESHOLD
        picks = AverageWithoutMisery(threshold).recommend(data, top_k=group_k)
        for pid in data.poi_ids:  # show which candidates the veto removed, and for whom
            j = col[pid]
            if S[:, j].min() < threshold:
                u = int(S[:, j].argmin())
                removed.append({"poi_id": pid, "stage": "AWM", "rule": "misery",
                                "detail": f"{uids[u]} {S[u, j]:.2f} < {threshold}"})
    else:
        # Legacy TF-IDF average is retained; it applies no satisfaction veto.
        if individual_method == 'tfidf':
            from ourney_travelsync.common import ranked_results
            picks = ranked_results(data.poi_ids, S.mean(axis=0), group_k)
        else:
            picks = AverageWithoutMisery(0.0).recommend(data, top_k=group_k)

    # Who each GFAR pick mainly served (the member with the largest share of its gain at selection time).
    rel = borda(S, GFAR_DEPTH)
    uncovered = np.ones(len(uids))
    rows = {r["place_id"]: r for r in P.records}
    group, k = [], len(picks)
    for rank, p in enumerate(picks, start=1):
        j = col[p.poi_id]
        picked_for = None
        if method == "gfar":
            share = uncovered * rel[:, j]
            picked_for = uids[int(np.argmax(share))] if share.max() > 0 else None
            uncovered *= 1.0 - rel[:, j]
        feats = {f: int(float(rows[p.poi_id][f])) for f in FEATURE_NAMES}
        scoring_reasons = []
        if individual_method == 'e5_hybrid':
            any_hybrid = any(s['effective_scoring_method']=='e5_hybrid' for s in data.scoring.diagnostics['members'])
            scoring_reasons = [{'code':'hybrid_relevance' if any_hybrid else 'feature_fallback',
                                'text': ('Structured preferences and semantic relevance guide the ranking; fallback is recorded per member.'
                                         if any_hybrid else 'Feature preferences guide this ranking because semantic scoring was unavailable or neutral.')}]
        group.append({
            "poi_id": p.poi_id, "group_rank": rank,
            "mean_score": round(float(S[:, j].mean()), 4),
            "selection_gain": round(p.score, 6) if method == "gfar" else None,
            "group_utility": utility(rank, k),
            "member_scores": {u: round(float(S[i, j]), 4) for i, u in enumerate(uids)},
            "picked_for": picked_for,
            "reasons": reasons(feats, members, picked_for) + scoring_reasons,
        })

    individual = {}
    for i, u in enumerate(uids):
        order = ranking(S[i], data.poi_ids, seed)[:top_l]
        individual[u] = [{"poi_id": data.poi_ids[j], "score": round(float(S[i, j]), 4), "rank": r,
                          **(data.scoring.components(i, j) if data.scoring else {}),
                          **({'description_backed': data.scoring.description_backed[j]} if data.scoring else {})}
                         for r, j in enumerate(order, start=1)]
    chosen = [col[g["poi_id"]] for g in group]
    sat = {u: (round(float(S[i, chosen].mean()), 4) if chosen and scale == '1-5' else None) for i, u in enumerate(uids)}
    cover = {u: round(float(rel[i, chosen].sum()), 4) if chosen else 0.0 for i, u in enumerate(uids)}

    return {
        "schema_version": req.get("schema_version", "rec-1.0"), "model_version": MODEL_VERSION,
        "dataset_version": req['dataset_version'], "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "method": method, 'individual_method': individual_method, "score_scale": scale, "tie_seed": seed,
        'scorer_version': data.scoring.diagnostics['scoring_version'] if data.scoring else 'method-c-char23-v1',
        'requested_scoring_method': requested_method,
        'scoring': ({**data.scoring.diagnostics,
                     'members': dict(zip(uids, data.scoring.diagnostics['members']))} if data.scoring else None),
        'score_semantics': 'relative ranking relevance, not satisfaction' if scale != '1-5' else 'feature-preference fit proxy',
        'status': 'ok' if k else 'exhausted',
        'exhaustion_reason': None if k else ('hard_constraints' if len(excluded) == len(P.place_ids)
                                            else 'awm_threshold' if method == 'awm' else 'no_candidates'),
        "group": group, "removed": removed, "individual": individual,
        "metrics": {
            "eligible": len(P.place_ids) - len(excluded), "candidate_pool": len(data.poi_ids), "returned": k,
            "per_member_relevance": cover, "min_member_relevance": min(cover.values()) if cover else 0,
            "member_satisfaction": sat,  # predicted: mean 1-5 score of the returned places per member
            "min_member_satisfaction": min((v for v in sat.values() if v is not None), default=None),
        },
    }


@app.get("/health")
def health():
    return {"ok": True, "model_version": MODEL_VERSION, "dataset_version": DATASET_VERSION,
            "places": len(places().place_ids)}
