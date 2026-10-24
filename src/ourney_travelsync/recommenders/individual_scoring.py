"""Rule-based baseline: mean survey preference over a POI's active features."""

from collections.abc import Mapping

import numpy as np

from ..common import FloatArray
from ..features import feature_vector, validate_features


def score_individuals(user_preferences: FloatArray, poi_features: FloatArray) -> FloatArray:
    """Return [members, POIs] scores on 1-5, preserving the binary vectors.

    score(u, p) = dot(preference_u, feature_p) / sum(feature_p).
    Averaging prevents multi-label POIs from winning merely by label count.
    Survey values stay on their original scale; no soft features are inferred.
    """
    users = np.asarray(user_preferences, dtype=float)
    pois = np.asarray(poi_features, dtype=float)
    validate_features(users, preferences=True)
    validate_features(pois, preferences=False)
    return (users @ pois.T) / pois.sum(axis=1)


def calculate_preference_score(
    user_preferences: Mapping[str, float],
    poi_features: Mapping[str, float],
) -> float:
    """Score one POI using the exact ten preference and binary feature keys."""
    user = feature_vector(user_preferences, preferences=True)
    poi = feature_vector(poi_features, preferences=False)
    return float(score_individuals(user[None, :], poi[None, :])[0, 0])
