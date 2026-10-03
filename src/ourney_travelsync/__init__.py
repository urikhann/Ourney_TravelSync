"""Group recommenders sharing the same input and output contract."""

from importlib import import_module
from .common import GroupInput, GroupRecommender, Recommendation

__all__ = ["AverageWithoutMisery", "GFAR", "FeatureAGREE", "GroupInput",
           "GroupRecommender", "Recommendation"]

_RECOMMENDER_MODULES = {
    "AverageWithoutMisery": ".RecSys.AvgWithoutMisery",
    "GFAR": ".RecSys.GFAR",
    "FeatureAGREE": ".RecSys.AGREE",
}


def __getattr__(name: str):
    """Load recommenders on demand so module execution does not preload them."""
    
    if name not in _RECOMMENDER_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    
    recommender = getattr(import_module(_RECOMMENDER_MODULES[name], __name__), name)
    globals()[name] = recommender
    
    return recommender


def main() -> None:
    print("Hello from ourney-travelsync!")
