"""10D preferences -> individual scores -> candidate union -> aggregation."""

from collections.abc import Collection, Mapping

import numpy as np

from ..common import GroupInput, GroupRecommender, Recommendation, validate_top_k
from ..features import feature_vector
from ..places import PlaceDataset
from .gfar import GFAR
from .individual_scoring import score_individuals
from .ties import ranking, tie_keys


def generate_candidates(data: GroupInput, *, individual_top_n: int = 100, tie_seed: int = 0) -> GroupInput:
    """Union each member's top-N POIs with identity-based seeded tie ordering.

    Every member's score is retained for every selected candidate, including
    POIs nominated by other members. Output rows use the same seeded identity order.
    """
    validate_top_k(individual_top_n)
    data.validate(require_scores=True)
    assert data.individual_scores is not None

    selected = np.zeros(len(data.poi_ids), dtype=bool)
    for ratings in data.individual_scores:
        selected[ranking(ratings, data.poi_ids, tie_seed)[:individual_top_n]] = True

    keys = tie_keys(data.poi_ids, tie_seed)
    indices = sorted(np.flatnonzero(selected), key=lambda i: (keys[i], data.poi_ids[i]))

    return GroupInput(
        data.user_ids,
        tuple(data.poi_ids[i] for i in indices),
        data.user_features,
        data.poi_features[indices],
        data.individual_scores[:, indices],
    )


def prepare_group(
    places: PlaceDataset,
    user_preferences: Mapping[str, Mapping[str, float]],
    *,
    individual_top_n: int = 100,
    tie_seed: int = 0,
    excluded_place_ids: Collection[str] = (),
    eligible_place_ids: Collection[str] | None = None,
) -> GroupInput:
    """Apply explicit eligibility/exclusions before scoring and candidate selection.

    External constraint logic may supply eligible IDs for known movement or
    other restrictions. Budget/date remain separate from the feature vector;
    price and opening-hour filtering require data we do not yet have.
    """

    if not user_preferences:
        raise ValueError("A group must contain at least one member.")
    users = np.vstack([feature_vector(value, preferences=True) for value in user_preferences.values()])
    excluded = set(excluded_place_ids)
    eligible = None if eligible_place_ids is None else set(eligible_place_ids)

    mask = np.asarray([
        poi_id not in excluded and (eligible is None or poi_id in eligible)
        for poi_id in places.place_ids
    ], dtype=bool)
    pois = places.features[mask]

    data = GroupInput(
        tuple(user_preferences),
        tuple(poi_id for poi_id, keep in zip(places.place_ids, mask) if keep),
        users, pois, score_individuals(users, pois),
    )
    return generate_candidates(data, individual_top_n=individual_top_n, tie_seed=tie_seed)


def recommend_group(
    places: PlaceDataset,
    user_preferences: Mapping[str, Mapping[str, float]],
    *,
    top_k: int = 10,
    individual_top_n: int = 100,
    tie_seed: int = 0,
    recommender: GroupRecommender | None = None,
    excluded_place_ids: Collection[str] = (),
    eligible_place_ids: Collection[str] | None = None,
) -> list[Recommendation]:
    """Use GFAR by default; an AWM baseline may be passed as recommender."""

    validate_top_k(top_k)
    data = prepare_group(
        places, user_preferences, individual_top_n=individual_top_n, tie_seed=tie_seed,
        excluded_place_ids=excluded_place_ids, eligible_place_ids=eligible_place_ids,
    )

    model = GFAR() if recommender is None else recommender
    return model.recommend(data, top_k=top_k)
