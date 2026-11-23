"""Exclusive visit roles derived from the existing imported feature taxonomy.

A cafe keeps its role even when both source features are present. The meal
policy separately permits evidenced breakfast coverage without reclassification.
Activity categories use the fixed feature order so one visit counts once.
"""
import re

ACTIVITY_CATEGORIES = ("nature", "history", "art", "shopping", "activity", "entertainment", "relax", "festival", "other")
EVENING_START_MIN = 18 * 60
_NIGHTLIFE = re.compile(r"술집|주점|호프|포차|이자카야|와인바|칵테일바|맥주전문|펍|\b(?:pubs?|bars?|izakaya)\b", re.I)


def is_evening_venue(record):
    # Use explicit source categories/tags, not a broad match on venue names.
    return "alcohol" in (record.get("tags") or ()) or bool(_NIGHTLIFE.search(
        str(record.get("category_name") or record.get("category") or "")))


def visit_role(features):
    if is_evening_venue(features):
        return "evening", "other"
    if float(features.get("cafe", 0) or 0) > 0:
        return "cafe", "other"
    if float(features.get("food", 0) or 0) > 0:
        return "meal", "other"
    category = next((f for f in ACTIVITY_CATEGORIES[:-1] if float(features.get(f, 0) or 0) > 0), "other")
    return "activity", category
