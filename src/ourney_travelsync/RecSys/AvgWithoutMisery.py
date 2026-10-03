"""Average Without Misery: reject vetoed POIs, then average member ratings."""

import numpy as np

from ..common import GroupInput, Recommendation, ranked_results, validate_top_k


class AverageWithoutMisery:
    """Baseline for calibrated individual preference scores.

    A POI is eligible when every member's score is >= misery_threshold.
    No fallback relaxes this threshold if fewer than top_k POIs survive.
    TODO: select the threshold on validation data, on the same rating scale.
    """

    def __init__(self, misery_threshold: float = 2.5) -> None:
        if not np.isfinite(misery_threshold):
            raise ValueError("misery_threshold must be finite.")
        
        self.misery_threshold = misery_threshold

    def recommend(self, data: GroupInput, *, top_k: int = 10) -> list[Recommendation]:
        """Average scores only for POIs that cause no member misery."""
        
        validate_top_k(top_k)
        data.validate(require_scores=True)
        
        ratings = data.individual_scores
        assert ratings is not None  # Established by validation.
        
        scores = ratings.mean(axis=0)
        scores[np.any(ratings < self.misery_threshold, axis=0)] = -np.inf
        
        return ranked_results(data.poi_ids, scores, top_k)
