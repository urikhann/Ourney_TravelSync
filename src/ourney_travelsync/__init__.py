"""Group recommenders sharing the same input and output contract."""

from importlib import import_module
from .common import GroupInput, GroupRecommender, Recommendation
from .features import FEATURE_NAMES
from .places import PlaceDataset, load_places

__all__ = ["AverageWithoutMisery", "GFAR", "GroupInput",
           "GroupRecommender", "Recommendation", "FEATURE_NAMES", "PlaceDataset", "load_places"]

_RECOMMENDER_MODULES = {
    "AverageWithoutMisery": ".recommenders.average_without_misery",
    "GFAR": ".recommenders.gfar",
}


def __getattr__(name: str):
    """Load recommenders on demand so module execution does not preload them."""
    
    if name not in _RECOMMENDER_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    
    recommender = getattr(import_module(_RECOMMENDER_MODULES[name], __name__), name)
    globals()[name] = recommender
    
    return recommender
