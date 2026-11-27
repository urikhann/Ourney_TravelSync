"""Explicit prototype dwell rules, never represented as verified place facts."""
from .planning_categories import visit_role

RULE_VERSION = "category-dwell-1.1"
DEFAULT_MINUTES = {
    "cafe": 45, "meal": 60, "evening": 60, "nature": 60, "history": 90, "art": 90,
    "shopping": 90, "activity": 60, "entertainment": 90, "relax": 60,
    "festival": 90, "other": 60,
}


def named_duration(name):
    """Name-based planning defaults, not verified venue requirements."""
    return 180 if any(term in (name or "") for term in ("찜질방", "온천")) else None


def default_duration(features, name=None):
    minutes = named_duration(name)
    if minutes is not None:
        return minutes
    role, category = visit_role(features)
    return DEFAULT_MINUTES[role if role != "activity" else category]
