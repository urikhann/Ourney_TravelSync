"""Exclusive visit roles derived from the existing imported feature taxonomy.

A cafe is never counted as a meal, even when both source features are present.
Activity categories use the fixed feature order so one visit counts once.
"""
ACTIVITY_CATEGORIES = ("nature", "history", "art", "shopping", "activity", "entertainment", "relax", "festival", "other")


def visit_role(features):
    if float(features.get("cafe", 0) or 0) > 0:
        return "cafe", "other"
    if float(features.get("food", 0) or 0) > 0:
        return "meal", "other"
    category = next((f for f in ACTIVITY_CATEGORIES[:-1] if float(features.get(f, 0) or 0) > 0), "other")
    return "activity", category
