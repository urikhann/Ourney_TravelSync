"""Feature-based AGREE-inspired forward-pass skeleton, not a reproduction.

Uses learned-style feature projections and POI-conditioned member attention.
NumPy keeps the starter runnable; weights are random and NOT trained.
TODO: port these layers to PyTorch for autograd, training, and checkpoints.
"""

import numpy as np

from ..common import FloatArray, GroupInput, Recommendation, ranked_results, validate_top_k


class FeatureAGREE:
    """Project TravelSync features instead of looking up user/POI ID embeddings.

    Features may have different widths, but their encoded latent widths match.
    This adaptation omits the original persistent group-ID embedding so new
    travel groups can be represented through their members alone.
    Random-weight outputs are only for testing data flow, never evaluation.
    """

    def __init__(self, user_feature_dim: int, poi_feature_dim: int,
                 latent_dim: int = 16, *, seed: int = 42) -> None:
        for dim in (user_feature_dim, poi_feature_dim, latent_dim):
            if isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0:
                raise ValueError("Feature and latent dimensions must be positive integers.")
        self.user_feature_dim = user_feature_dim
        self.poi_feature_dim = poi_feature_dim
        rng = np.random.default_rng(seed)
        self.user_projection = rng.normal(0, 0.1, (user_feature_dim, latent_dim))
        self.poi_projection = rng.normal(0, 0.1, (poi_feature_dim, latent_dim))
        self.attention_projection = rng.normal(0, 0.1, (2 * latent_dim, latent_dim))
        self.attention_vector = rng.normal(0, 0.1, latent_dim)
        self.output_projection = rng.normal(0, 0.1, (3 * latent_dim, latent_dim))
        self.output_vector = rng.normal(0, 0.1, latent_dim)

    def fit(self, training_data: object) -> None:
        """Reserved training hook; intentionally refuses to fake model training.

        TODO: define labelled group–POI examples and user–POI examples.
        TODO: implement joint individual/group loss and negative sampling.
        TODO: split by users/groups and time before fitting feature transforms.
        TODO: validate, checkpoint, and expose training status before evaluation.
        """
        raise NotImplementedError("Training is not implemented in this NumPy skeleton.")

    def forward(self, data: GroupInput) -> tuple[FloatArray, FloatArray]:
        """Return raw scores [POIs] and attention [POIs, members].

        Attention softmax is over members separately for each candidate POI.
        TODO: add biases, dropout, batching, and an individual prediction head.
        """
        data.validate()
        if (data.user_features.shape[1] != self.user_feature_dim
                or data.poi_features.shape[1] != self.poi_feature_dim):
            raise ValueError("Feature widths must match model initialization.")
        users = np.tanh(data.user_features @ self.user_projection)
        pois = np.tanh(data.poi_features @ self.poi_projection)
        scores = np.empty(len(pois))
        attention = np.empty((len(pois), len(users)))
        for i, poi in enumerate(pois):
            repeated_poi = np.broadcast_to(poi, users.shape)
            paired = np.concatenate((users, repeated_poi), axis=1)
            logits = np.tanh(paired @ self.attention_projection) @ self.attention_vector
            weights = np.exp(logits - logits.max())
            weights /= weights.sum()
            attention[i] = weights
            group = weights @ users
            interaction = np.concatenate((group, poi, group * poi))
            scores[i] = np.tanh(interaction @ self.output_projection) @ self.output_vector
        return scores, attention

    def recommend(self, data: GroupInput, *, top_k: int = 10) -> list[Recommendation]:
        """Rank raw untrained utility scores for a forward-pass smoke test."""
        validate_top_k(top_k)
        scores, _ = self.forward(data)
        return ranked_results(data.poi_ids, scores, top_k)
