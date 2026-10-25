"""Predicted satisfaction on shared 1–5 scores, not ground-truth relevance."""


def evaluate_satisfaction(data, recommendations):
    """Evaluate member means; empty lists have undefined satisfaction (None)."""
    data.validate(require_scores=True)
    columns_by_id = {poi_id: i for i, poi_id in enumerate(data.poi_ids)}
    ids = [result.poi_id for result in recommendations]
    if len(set(ids)) != len(ids):
        raise ValueError("A recommendation list must contain unique POIs.")
    if any(poi_id not in columns_by_id for poi_id in ids):
        raise ValueError("Recommendation contains an unknown POI.")
    if not ids:
        return dict(returned_pois=0, mean_satisfaction=None,
                    minimum_member_satisfaction=None, member_satisfaction=dict.fromkeys(data.user_ids))
    means = data.individual_scores[:, [columns_by_id[identity] for identity in ids]].mean(axis=1)
    return dict(returned_pois=len(ids), mean_satisfaction=float(means.mean()),
                minimum_member_satisfaction=float(means.min()),
                member_satisfaction=dict(zip(data.user_ids, means.tolist())))


def candidate_tie_diagnostics(seed=0, top_n=100):
    """Compare historical row-order cutoffs with identity-seeded cutoffs on v2.

    These are exposure diagnostics under category scoring, not quality/fairness metrics.
    """
    from collections import Counter
    import numpy as np
    from .places import load_places
    from .recommenders.individual_scoring import score_individuals
    from .recommenders.ties import ranking
    places = load_places()
    scenarios = {'equal': [[3]*10, [3]*10],
                 'varied': [[4,5,3,2,2,3,3,3,4,1], [3,2,5,4,3,2,4,2,5,3]]}
    report = dict(rows=len(places.place_ids), distinct_feature_vectors=len(set(map(tuple, places.features))),
                  seed=seed, candidate_top_n=top_n, scenarios={})
    for name, users in scenarios.items():
        scores = score_individuals(np.asarray(users, dtype=float), places.features)
        before, after, metrics = set(), set(), []
        for ratings in scores:
            old = np.argsort(-ratings, kind='stable')[:top_n]
            new = ranking(ratings, places.place_ids, seed)[:top_n]
            before.update(map(int,old)); after.update(map(int,new))
            counts = Counter(ratings)
            cutoff = float(ratings[old[-1]]) if len(old) else None
            metrics.append(dict(unique_scores=len(counts), tied_rows=sum(c for c in counts.values() if c>1),
                                cutoff_score=cutoff, cutoff_tied_rows=counts[cutoff] if cutoff is not None else 0))
        def composition(indices):
            return dict(candidates=len(indices), sources=dict(sorted(Counter(places.records[i]['source'] for i in indices).items())))
        report['scenarios'][name] = dict(members=metrics, before=composition(before), after=composition(after))
    return report
