"""GFAR starter: normalized Borda relevance and greedy coverage gains.

Reference: Kaya, Bridge & Tintarev (RecSys 2020), equations 1 and 5.
https://doi.org/10.1145/3383313.3412232
"""

import numpy as np

from ..common import GroupInput, Recommendation, validate_top_k


class GFAR:
    """Build a list by balancing relevance across its prefixes.

    Each member's top-L list gives Borda weights L-1, ..., 0, normalized
    per member. Items outside that list have zero relevance. Ties follow
    input POI order. L is independent of the requested output length.
    TODO: tune individual_top_n and validate against the authors' code.
    """

    def __init__(self, individual_top_n: int = 20) -> None:
        validate_top_k(individual_top_n)
        
        if individual_top_n < 2:
            raise ValueError("individual_top_n must be at least 2 for Borda weights.")
        
        self.individual_top_n = individual_top_n

    def recommend(self, data: GroupInput, *, top_k: int = 10) -> list[Recommendation]:
        """Return greedy selection order; scores are gains at selection time."""
        
        validate_top_k(top_k)
        data.validate(require_scores=True)
        
        ratings = data.individual_scores
        assert ratings is not None
        
        n_users, n_pois = ratings.shape
        
        if n_pois == 0 or top_k == 0:
            return []
        
        if n_pois == 1:
            # Explicit extension: paper's Borda normalization is undefined for L=1.
            return [Recommendation(data.poi_ids[0], float(n_users))]
        
        length = min(self.individual_top_n, n_pois)
        relevance = np.zeros_like(ratings, dtype=float)
        weights = np.arange(length - 1, -1, -1, dtype=float)
        weights /= weights.sum()
        
        for u in range(n_users):
            order = np.argsort(-ratings[u], kind="stable")[:length]
            relevance[u, order] = weights

        uncovered = np.ones(n_users)
        available = np.ones(n_pois, dtype=bool)
        results: list[Recommendation] = []
        
        for _ in range(min(top_k, n_pois)):
            gains = uncovered @ relevance
            gains[~available] = -np.inf
            
            chosen = int(np.argmax(gains))
            results.append(Recommendation(data.poi_ids[chosen], float(gains[chosen])))
            
            uncovered *= 1.0 - relevance[:, chosen]
            available[chosen] = False
        return results
