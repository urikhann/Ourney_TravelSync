"""Offline-testable conversion of captured DB inputs to recommendation inputs."""
from dataclasses import dataclass
import numpy as np
from ..features import FEATURE_NAMES, feature_vector
from ..places import PlaceDataset
from ..recommenders.pipeline import prepare_group
from ..recommenders.individual_scoring import score_individuals
from ..recommenders.ties import ranking


@dataclass(frozen=True)
class SnapshotInput:
    places: PlaceDataset
    preferences: dict
    excluded_ids: tuple[str, ...]
    dataset_version: str


def adapt_run_inputs(inputs):
    """Sort DB identities and validate captured preferences and exclusions.

    Exclusions accept database poi_id strings and exact category/category_name
    values. Timing, cost, accessibility and routing feasibility are not inferred.
    """
    run = inputs['run']
    dataset = str(run['dataset_version'])
    expected = {str(m['user_id']): m['preference_version'] for m in run['member_snapshot']}
    if not expected or len(expected) != len(run['member_snapshot']):
        raise ValueError('INVALID_MEMBER_SNAPSHOT')
    preferences = {}
    for row in sorted(inputs['preferences'], key=lambda r: str(r['user_id'])):
        uid = str(row['user_id'])
        if uid in preferences or uid not in expected or row['version'] != expected[uid]:
            raise ValueError('PREFERENCE_SNAPSHOT_MISMATCH')
        values = {name: row.get(name) for name in FEATURE_NAMES}
        if any(type(v) is not int for v in values.values()):
            raise ValueError('MISSING_OR_NONINTEGER_PREFERENCE')
        feature_vector(values, preferences=True)
        preferences[uid] = values
    if set(preferences) != set(expected):
        raise ValueError('MEMBER_PREFERENCE_MISSING')
    constraints = inputs['constraints']
    if constraints is None:
        raise ValueError('CONSTRAINT_SNAPSHOT_MISSING')
    exclusions = constraints.get('member_exclusions') or {}
    if set(exclusions) - set(expected):
        raise ValueError('EXCLUSION_USER_NOT_IN_SNAPSHOT')
    excluded, categories = set(), set()
    for rule in exclusions.values():
        if not isinstance(rule, dict) or set(rule) - {'poi_ids', 'categories'}:
            raise ValueError('INVALID_EXCLUSIONS')
        for field in ('poi_ids', 'categories'):
            values = rule.get(field, [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError('INVALID_EXCLUSIONS')
        excluded.update(rule.get('poi_ids', []))
        categories.update(rule.get('categories', []))
    records, ids, features, coordinates = [], [], [], []
    for row in sorted(inputs['pois'], key=lambda r: str(r['poi_id'])):
        if str(row['dataset_version']) != dataset:
            raise ValueError('POI_DATASET_MISMATCH')
        pid = str(row['poi_id'])
        if pid in ids or not pid.strip() or not str(row.get('place_id', '')).strip():
            raise ValueError('INVALID_POI_IDENTITY')
        vector = feature_vector({f: row.get(f) for f in FEATURE_NAMES}, preferences=False)
        coord = [float(row['latitude']), float(row['longitude'])]
        if not np.isfinite(coord).all() or abs(coord[0]) > 90 or abs(coord[1]) > 180:
            raise ValueError('INVALID_POI_COORDINATES')
        if not row.get('active', True) or row.get('exclude_reason'):
            excluded.add(pid)
        if row.get('category') in categories or row.get('category_name') in categories:
            excluded.add(pid)
        records.append(dict(row)); ids.append(pid); features.append(vector); coordinates.append(coord)
    places = PlaceDataset(tuple(records), tuple(ids), np.asarray(features, dtype=float).reshape(-1, 10),
                          np.asarray(coordinates, dtype=float).reshape(-1, 2))
    return SnapshotInput(places, preferences, tuple(sorted(excluded)), dataset)


def compute_snapshot(inputs):
    """Compute only from captured config/data/preferences/constraints."""
    from .. import AverageWithoutMisery, GFAR
    snapshot = adapt_run_inputs(inputs)
    config = inputs['run']['config_snapshot']
    seed, algorithm = config['tie_seed'], config['algorithm']
    if algorithm not in ('awm', 'gfar'):
        raise ValueError('UNKNOWN_ALGORITHM')
    group = prepare_group(snapshot.places, snapshot.preferences,
                          excluded_place_ids=snapshot.excluded_ids,
                          individual_top_n=config['candidate_top_n'], tie_seed=seed)
    model = (GFAR(config['gfar_depth']) if algorithm == 'gfar'
             else AverageWithoutMisery(config['misery_threshold']))
    results = model.recommend(group, top_k=config['top_k'])
    users = np.asarray([[v[f] for f in FEATURE_NAMES] for v in snapshot.preferences.values()], dtype=float)
    scores = score_individuals(users, snapshot.places.features)
    individual, excluded = [], set(snapshot.excluded_ids)
    for u, uid in enumerate(snapshot.preferences):
        eligible = [i for i, pid in enumerate(snapshot.places.place_ids) if pid not in excluded]
        order = ranking(scores[u, eligible], tuple(snapshot.places.place_ids[i] for i in eligible), seed)
        ranks = {eligible[index]: rank for rank, index in enumerate(order, 1)}
        for p, pid in enumerate(snapshot.places.place_ids):
            individual.append(dict(user_id=uid, poi_id=pid, score=float(scores[u, p]),
                                   rank=ranks.get(p), eligible=pid not in excluded))
    columns = {pid: i for i, pid in enumerate(snapshot.places.place_ids)}
    rows = [dict(poi_id=r.poi_id, mean_score=float(scores[:, columns[r.poi_id]].mean()),
                 group_rank=rank, selection_gain=r.score if algorithm == 'gfar' else None,
                 group_utility=None, feasible_dates=None) for rank, r in enumerate(results, 1)]
    return results, individual, rows


def recommend_database(conn, group_id, constraint_id, config, dataset_version=None):
    """Capture and persist one run atomically; caller commits outer connection."""
    from .repository import (create_recommendation_run, get_run_inputs,
                             save_individual_scores, save_group_scores)
    with conn.transaction():
        run_id = create_recommendation_run(conn, group_id, constraint_id, config, dataset_version)
        conn.execute("UPDATE recommendation_runs SET status='RUNNING' WHERE run_id=%s", (run_id,))
        results, individual, group = compute_snapshot(get_run_inputs(conn, run_id))
        save_individual_scores(conn, run_id, individual)
        save_group_scores(conn, run_id, group)
        conn.execute("UPDATE recommendation_runs SET status='COMPLETED' WHERE run_id=%s", (run_id,))
        return run_id, results
