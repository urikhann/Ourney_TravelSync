"""Explicit day preferences; metadata estimates are not verified physical exposure."""


def visit_burden(candidate, preference):
    duration = candidate.duration_min.value or 0
    if preference == "indoor":
        # Unknown indoor status must not look like confirmed shelter.
        return 0 if candidate.indoor is True else duration
    level = candidate.walking_level if candidate.walking_level is not None else 1
    return round(duration * level * 100)


def day_burden(req, day, preference):
    candidates = {p.poi_id: p for p in req.candidates}
    burden = sum(visit_burden(candidates[v.poi_id], preference) for v in day.items)
    if preference == "less_walking":
        burden += round(day.walk_minutes * 100)
    return burden
