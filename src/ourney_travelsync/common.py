"""Shared data contract; candidate filtering happens before this layer."""

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class GroupInput:
    """One group and its eligible POIs, in a fixed row/column order.

    user_features: [members, user_feature_dim].
    poi_features: [candidates, poi_feature_dim].
    individual_scores: optional [members, candidates], required by AWM/GFAR.
    AWM assumes calibrated ratings (e.g. 1–5); GFAR uses their ordering only.
    FeatureAGREE uses features directly and ignores individual_scores.
    IDs identify outputs only; they are never learned embedding-table indices.
    TODO: freeze feature order, categorical encoding, and training-only scaling.
    """

    user_ids: tuple[str, ...]
    poi_ids: tuple[str, ...]
    user_features: FloatArray
    poi_features: FloatArray
    individual_scores: FloatArray | None = None

    def validate(self, *, require_scores: bool = False) -> None:
        """Reject ambiguous alignment, missing scores, and nonfinite values."""
        
        if not self.user_ids:
            raise ValueError("A group must contain at least one member.")
        
        for ids in (self.user_ids, self.poi_ids):
            if len(set(ids)) != len(ids):
                raise ValueError("User and POI IDs must be unique within each list.")
            
        for features, rows in ((self.user_features, len(self.user_ids)),
                               (self.poi_features, len(self.poi_ids))):
            if features.ndim != 2 or features.shape[0] != rows or features.shape[1] == 0:
                raise ValueError("Features must be 2-D with aligned rows and positive width.")
            if not np.isfinite(features).all():
                raise ValueError("Features must contain finite numeric values.")
            
        if require_scores and self.individual_scores is None:
            raise ValueError("This model requires individual preference scores.")
        
        if self.individual_scores is not None:
            if self.individual_scores.shape != (len(self.user_ids), len(self.poi_ids)):
                raise ValueError("Scores must have shape [members, candidates].")
            if not np.isfinite(self.individual_scores).all():
                raise ValueError("Scores must contain finite values.")


@dataclass(frozen=True)
class Recommendation:
    """A POI in selection order, with a model-specific diagnostic score.

    AWM: mean rating. GFAR: marginal gain at selection. AGREE: raw utility.
    Scores are not comparable across models; evaluate the ranked POI IDs.
    """

    poi_id: str
    score: float


class GroupRecommender(Protocol):
    """Minimal common interface for evaluation and itinerary handoff."""

    def recommend(self, data: GroupInput, *, top_k: int = 10) -> list[Recommendation]:
        """Return up to top_k eligible POIs, ordered best first."""
        ...


def validate_top_k(top_k: int) -> None:
    """Zero returns an empty list; negative or noninteger limits are invalid."""
    
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 0:
        raise ValueError("top_k must be a nonnegative integer.")


def ranked_results(ids: tuple[str, ...], scores: FloatArray, top_k: int) -> list[Recommendation]:
    """Rank static scores, breaking ties by original candidate order."""
    
    order = np.argsort(-scores, kind="stable")
    return [Recommendation(ids[i], float(scores[i]))
            for i in order if np.isfinite(scores[i])][:top_k]
