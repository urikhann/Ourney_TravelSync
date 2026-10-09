"""Fixed feature contract for rule-mapped POIs and survey preferences."""

from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

FEATURE_NAMES = (
    "food", "cafe", "nature", "history", "art", "shopping",
    "activity", "entertainment", "relax", "festival",
)


def validate_features(values: NDArray[np.float64], *, preferences: bool) -> None:
    """Validate a matrix in FEATURE_NAMES order, including empty POI matrices."""
    if values.ndim != 2 or values.shape[1] != len(FEATURE_NAMES):
        raise ValueError("Feature matrices must have exactly 10 columns in FEATURE_NAMES order.")
    if not np.isfinite(values).all():
        raise ValueError("Features must contain finite numeric values.")
    if preferences:
        if np.any((values < 1) | (values > 5)):
            raise ValueError("User preferences must be between 1 and 5.")
    else:
        if not np.isin(values, (0, 1)).all():
            raise ValueError("POI features must be independent binary 0/1 values.")
        if np.any(values.sum(axis=1) == 0):
            raise ValueError("Every POI must have at least one active feature.")


def feature_vector(values: Mapping[str, float], *, preferences: bool) -> NDArray[np.float64]:
    """Require the exact ten named dimensions; mapping order does not matter."""
    missing = set(FEATURE_NAMES) - values.keys()
    extra = values.keys() - set(FEATURE_NAMES)
    if missing or extra:
        raise ValueError(f"Expected the 10 TravelSync features; missing={sorted(missing)}, extra={sorted(extra)}")
    vector = np.asarray([values[name] for name in FEATURE_NAMES], dtype=float)
    validate_features(vector[None, :], preferences=preferences)
    return vector
