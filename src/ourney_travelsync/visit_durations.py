"""Explicit prototype dwell rules, never represented as verified place facts."""
from .planning_categories import visit_role

RULE_VERSION = "category-dwell-1.0"
DEFAULT_MINUTES = {
    "cafe": 45, "meal": 60, "nature": 60, "history": 90, "art": 90,
    "shopping": 60, "activity": 60, "entertainment": 90, "relax": 60,
    "festival": 90, "other": 60,
}


def default_duration(features):
    role, category = visit_role(features)
    return DEFAULT_MINUTES[role if role != "activity" else category]
