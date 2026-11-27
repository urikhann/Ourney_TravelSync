"""Waiting on selected POI-to-POI legs, using the validated arrival times."""
from .contracts import GapTarget, Lock
from ..place_identity import candidate_identity


def end_of_day_gaps(req, days):
    """Unused time after returning, bounded by everyone's actual availability."""
    from .eligibility import common_bounds
    bounds = common_bounds(req)
    requested = {d.date: d for d in req.days}
    gaps = []
    for day in days:
        bound = bounds.get(day.date)
        if not day.items or bound is None or day.end_arrival_min >= bound.end_min:
            continue
        start, end = day.end_arrival_min, bound.end_min
        rest = sum(max(0, min(end, w.end_min) - max(start, w.start_min))
                   for w in requested[day.date].breaks)
        if end - start <= rest:
            continue
        gaps.append(dict(date=day.date.isoformat(), after_poi_id=day.items[-1].poi_id,
            before_poi_id=day.end_point, start_min=start, end_min=end,
            duration_minutes=end-start, intentional_break_minutes=rest,
            unallocated_minutes=end-start-rest, reason="early_return", action="fill", kind="end_of_day"))
    return gaps


def fillable_gaps(req, days):
    return sorted((*waiting_gaps(req, days), *end_of_day_gaps(req, days)),
                  key=lambda g: (g.get("kind") != "end_of_day", -g["unallocated_minutes"]))


def gap_candidate(candidate, gap):
    return candidate.visit_category == "activity" or (
        gap.get("kind") == "end_of_day" and candidate.visit_category == "cafe")


def fits_gap_window(candidate, eligibility, gap, direct_minutes, incoming_minutes=0, outgoing_minutes=0):
    """Visit, arrival and onward driving must fit an eligible opening interval."""
    from datetime import date
    start = gap["start_min"] - direct_minutes + incoming_minutes
    end = gap["end_min"] - outgoing_minutes
    return any(min(end, w.end_min) - max(start, w.start_min) >= candidate.duration_min.value
               for w in eligibility.windows.get(date.fromisoformat(gap["date"]), ()))


def improves_gap(req, original, alternative):
    """Compare validated results in the optimizer's existing priority order."""
    if alternative.validation_status != "PASSED" or alternative.error_code:
        return False
    before = sum(g["unallocated_minutes"] for g in waiting_gaps(req, original.days))
    after = sum(g["unallocated_minutes"] for g in waiting_gaps(req, alternative.days))
    old_visits = {v.poi_id for day in original.days for v in day.items}
    new_visits = {v.poi_id for day in alternative.days for v in day.items}
    end_before = sum(g["unallocated_minutes"] for g in end_of_day_gaps(req, original.days))
    end_after = sum(g["unallocated_minutes"] for g in end_of_day_gaps(req, alternative.days))
    # Delaying existing visits or the return is not an added experience.
    improved = after < before or (end_after < end_before and bool(new_visits - old_visits))
    floors = {p.objective: p.objective_value for p in original.phases if p.objective != "waiting_minutes"}
    values = {p.objective: p.objective_value for p in alternative.phases}
    if any(v is None or values.get(n) is None for n, v in floors.items()):
        return False
    maxima = {"group_utility", "breakfast_and_group_utility", "optional_stops_and_group_utility", "minimum_relevance"}
    original_priority = tuple(v if n in maxima else -v for n, v in floors.items())
    new_priority = tuple(values[n] if n in maxima else -values[n] for n in floors)
    return improved and new_priority >= original_priority


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
    from .eligibility import check_eligibility
    gaps = fillable_gaps(req, result.days)
    gaps = [g for g in gaps if g["action"] in ("fill", "reoptimize")]
    if not gaps:
        return None
    selected = {v.poi_id for day in result.days for v in day.items}
    selected_identities = {candidate_identity(p) for p in req.candidates if p.poi_id in selected}
    locks = tuple(Lock(poi_id=v.poi_id, date=day.date, start_min=v.start_min)
                  for day in result.days for v in day.items)
    edges = {(e.date.isoformat(), e.origin, e.destination): e for e in req.edges if e.usable}
    eligibility = {p.poi_id: p for p in check_eligibility(req)}
    for gap in gaps:
        candidates = []
        for p in req.candidates:
            if candidate_identity(p) in selected_identities or not gap_candidate(p, gap) or not p.duration_min.value:
                continue
            incoming = edges.get((gap["date"], gap["after_poi_id"], p.poi_id))
            outgoing = edges.get((gap["date"], p.poi_id, gap["before_poi_id"]))
            direct = edges.get((gap["date"], gap["after_poi_id"], gap["before_poi_id"]))
            if incoming is None or outgoing is None or direct is None:
                continue
            if gap.get("kind") != "end_of_day" and max(incoming.duration_minutes, outgoing.duration_minutes) > req.gap_policy.nearby_leg_minutes:
                continue
            if (p.duration_min.value + incoming.duration_minutes + outgoing.duration_minutes <= gap["duration_minutes"] + direct.duration_minutes
                    and fits_gap_window(p, eligibility[p.poi_id], gap, direct.duration_minutes,
                                        incoming.duration_minutes, outgoing.duration_minutes)):
                candidates.append(p.poi_id)
        if candidates:
            # The target starts at the previous POI's departure. Route constraints
            # account for both new legs, including travel before the old arrival.
            direct = edges[gap["date"], gap["after_poi_id"], gap["before_poi_id"]]
            target = GapTarget(date=gap["date"], start_min=gap["start_min"] - direct.duration_minutes,
                               end_min=gap["end_min"], candidate_ids=tuple(candidates))
            return req.model_copy(update={"search_seconds": seconds, "locked_items": locks,
                                          "gap_target": target})
    if any(g["action"] == "reoptimize" for g in gaps):
        return req.model_copy(update={"search_seconds": seconds})
    return None
