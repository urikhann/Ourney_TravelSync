"""Waiting on selected POI-to-POI legs, using the validated arrival times."""
from .contracts import GapTarget, Lock
from ..place_identity import candidate_identity


def improves_gap(req, original, alternative):
    """Compare validated results in the optimizer's existing priority order."""
    if alternative.validation_status != "PASSED" or alternative.error_code:
        return False
    before = sum(g["unallocated_minutes"] for g in waiting_gaps(req, original.days))
    after = sum(g["unallocated_minutes"] for g in waiting_gaps(req, alternative.days))
    floors = {p.objective: p.objective_value for p in original.phases if p.objective != "waiting_minutes"}
    values = {p.objective: p.objective_value for p in alternative.phases}
    if any(v is None or values.get(n) is None for n, v in floors.items()):
        return False
    maxima = {"group_utility", "breakfast_and_group_utility", "optional_stops_and_group_utility", "minimum_relevance"}
    original_priority = tuple(v if n in maxima else -v for n, v in floors.items())
    new_priority = tuple(values[n] if n in maxima else -values[n] for n in floors)
    return after < before and new_priority >= original_priority


def waiting_gaps(req, days):
    requested = {d.date: d for d in req.days}
    gaps = []
    for day in days:
        for previous, visit in zip(day.items, day.items[1:]):
            duration = visit.start_min - visit.arrival_min
            if duration <= 0:
                continue
            rest = sum(max(0, min(visit.start_min, w.end_min) - max(visit.arrival_min, w.start_min))
                       for w in requested[day.date].breaks)
            unallocated = duration - rest
            policy = req.gap_policy
            action = ("reoptimize" if unallocated > policy.reoptimize_minutes else
                      "fill" if unallocated > policy.rest_minutes else
                      "rest" if unallocated > policy.buffer_minutes else "buffer")
            reason = "rest" if rest == duration else "free_time"
            if not rest:
                candidate = next(p for p in req.candidates if p.poi_id == visit.poi_id)
                fixed = next((o for o in req.daily_composition.meal_overrides
                              if o.date == day.date and o.fixed_start_min == visit.start_min), None) if req.daily_composition else None
                if fixed and (candidate.visit_category == "meal" or (fixed.name == "breakfast" and visit.breakfast_status)):
                    reason = "fixed_" + fixed.name
                else:
                    operation = candidate.operations.get(day.date)
                    if req.enforce_opening_hours and operation and operation.openings.known and any(
                            w.start_min == visit.start_min and visit.departure_min <= w.end_min for w in operation.openings.value):
                        reason = "opening_time"
                    elif action == "buffer":
                        reason = "travel_buffer"
            gaps.append(dict(date=day.date.isoformat(), after_poi_id=previous.poi_id,
                             before_poi_id=visit.poi_id, start_min=visit.arrival_min,
                             end_min=visit.start_min, duration_minutes=duration,
                             intentional_break_minutes=rest, unallocated_minutes=unallocated,
                             reason=reason, action=action))
    return gaps


def gap_retry_request(req, result, seconds):
    """One repair using the existing preference-ranked, enriched candidate pool."""
    gaps = sorted(waiting_gaps(req, result.days), key=lambda g: g["unallocated_minutes"], reverse=True)
    gaps = [g for g in gaps if g["action"] in ("fill", "reoptimize")]
    if not gaps:
        return None
    selected = {v.poi_id for day in result.days for v in day.items}
    selected_identities = {candidate_identity(p) for p in req.candidates if p.poi_id in selected}
    locks = tuple(Lock(poi_id=v.poi_id, date=day.date, start_min=v.start_min)
                  for day in result.days for v in day.items)
    edges = {(e.date.isoformat(), e.origin, e.destination): e for e in req.edges if e.usable}
    for gap in gaps:
        candidates = []
        for p in req.candidates:
            if candidate_identity(p) in selected_identities or p.visit_category != "activity" or not p.duration_min.value:
                continue
            incoming = edges.get((gap["date"], gap["after_poi_id"], p.poi_id))
            outgoing = edges.get((gap["date"], p.poi_id, gap["before_poi_id"]))
            direct = edges.get((gap["date"], gap["after_poi_id"], gap["before_poi_id"]))
            if incoming is None or outgoing is None or direct is None:
                continue
            if max(incoming.duration_minutes, outgoing.duration_minutes) > req.gap_policy.nearby_leg_minutes:
                continue
            if p.duration_min.value + incoming.duration_minutes + outgoing.duration_minutes <= gap["duration_minutes"] + direct.duration_minutes:
                candidates.append(p.poi_id)
        if candidates:
            # The target starts at the previous POI's departure. Route constraints
            # account for both new legs, including travel before the old arrival.
            direct = edges[gap["date"], gap["after_poi_id"], gap["before_poi_id"]]
            target = GapTarget(date=gap["date"], start_min=gap["start_min"] - direct.duration_minutes,
                               end_min=gap["end_min"], candidate_ids=tuple(candidates))
            return req.model_copy(update={"search_seconds": seconds, "locked_items": locks,
                                          "gap_target": target})
    if gaps[0]["action"] == "reoptimize":
        return req.model_copy(update={"search_seconds": seconds})
    return None
