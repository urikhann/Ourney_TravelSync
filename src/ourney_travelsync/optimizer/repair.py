"""Bounded single-occurrence repair. All outputs pass the independent E3 gate."""
from dataclasses import dataclass

from .contracts import DayPlan, Lock, PlanRequest, Visit
from .eligibility import usable_cost, usable_duration
from .validation import validate_final


@dataclass(frozen=True)
class RepairResult:
    days: tuple[DayPlan, ...] = ()
    strategy: str = ""
    reason: str = "no_feasible_replacement"
    errors: tuple[str, ...] = ()


def rebuild_day(req, original, visits):
    """Reconstruct connections and accounting from evidence, never from cached totals."""
    candidates = {p.poi_id: p for p in req.candidates}
    edges = {(e.date, e.origin, e.destination): e for e in req.edges}
    previous, departure = original.start_point, original.start_min
    cost = travel = walk = 0
    items = []
    for old, start in visits:
        p = candidates[old.poi_id]
        e = edges.get((original.date, previous, p.poi_id))
        if e is None or not e.usable:
            return None
        if not usable_duration(p.duration_min):
            return None
        stay = p.duration_min.value
        arrival = departure + e.duration_minutes
        if not 0 <= arrival <= start < start + stay <= 1440:
            return None
        fee = len(req.members) * p.cost_per_person.value if usable_cost(p.cost_per_person) else 0
        items.append(Visit(poi_id=p.poi_id, arrival_min=arrival, start_min=start,
                           departure_min=start + stay, travel_minutes=e.duration_minutes,
                           stay_minutes=stay, visit_cost=fee, breakfast_status=old.breakfast_status))
        cost += fee + e.group_fare
        travel += e.duration_minutes
        walk += e.walk_minutes
        previous, departure = p.poi_id, start + stay
    e = edges.get((original.date, previous, original.end_point))
    if e is None or not e.usable or departure + e.duration_minutes > 1440:
        return None
    return DayPlan(date=original.date, start_point=original.start_point, end_point=original.end_point,
                   start_min=original.start_min, items=tuple(items), end_arrival_min=departure + e.duration_minutes,
                   return_travel_minutes=e.duration_minutes, total_cost=cost + e.group_fare,
                   travel_minutes=travel + e.duration_minutes, walk_minutes=walk + e.walk_minutes)


def repair(req: PlanRequest, days: tuple[DayPlan, ...], day, index: int, candidate_id: str,
           *, radius=1, shift_minutes=30, protected_ids=frozenset()) -> RepairResult:
    original = next(d for d in days if d.date == day)
    target = original.items[index]
    locked = {l.poi_id for l in req.locked_items if l.poi_id != candidate_id} | set(protected_ids)
    if target.poi_id in locked:
        return RepairResult(reason="locked_activity_conflict")
    if candidate_id in {v.poi_id for d in days for v in d.items}:
        return RepairResult(reason="duplicate_poi")
    candidate = next(p for p in req.candidates if p.poi_id == candidate_id)
    op = candidate.operations.get(day)
    if op is None or not op.openings.known or not op.closed.known:
        return RepairResult(reason="opening_hours_unknown")
    if op.closed.value:
        return RepairResult(reason="candidate_closed")
    if not usable_duration(candidate.duration_min):
        return RepairResult(reason="duration_unknown")
    if req.enforce_cost and not usable_cost(candidate.cost_per_person):
        return RepairResult(reason="cost_unknown")
    # Pin the selected replacement for all validation/solver paths.
    items = list(original.items)
    items[index] = target.model_copy(update={"poi_id": candidate_id, "breakfast_status": None})
    edges = {(e.origin, e.destination): e for e in req.edges if e.date == day and e.usable}
    errors = ()
    for strategy in ("fixed", "local"):
        first, last = (index, index) if strategy == "fixed" else (max(0, index-radius), min(len(items)-1, index+radius))
        visits, previous, departure = [], original.start_point, original.start_min
        for j, item in enumerate(items):
            start = item.start_min
            p = next(p for p in req.candidates if p.poi_id == item.poi_id)
            edge = edges.get((previous, item.poi_id))
            if edge is None or not usable_duration(p.duration_min):
                break
            arrival = departure + edge.duration_minutes
            if first <= j <= last and item.poi_id not in locked:
                operation = p.operations.get(day)
                # Unknown retained hours keep the existing policy, but never invent new windows.
                windows = operation.openings.value if operation and operation.openings.known else ()
                choices = [max(arrival, w.start_min, lower)
                           for w in windows for lower in ((start,) if strategy == "fixed" else (start, start-shift_minutes))]
                choices = [s for s in choices if any(w.start_min <= s and s+p.duration_min.value <= w.end_min for w in windows)
                           and (j == index or abs(s-start) <= shift_minutes)]
                if not choices:
                    break
                start = min(choices, key=lambda s: (abs(s-item.start_min), s))
            visits.append((item, start))
            previous, departure = item.poi_id, start + p.duration_min.value
        if len(visits) != len(items):
            continue
        changed = rebuild_day(req, original, visits)
        if changed is None:
            continue
        merged = tuple(changed if d.date == day else d for d in days)
        checked = validate_final(req, merged)
        if checked.passed:
            return RepairResult(merged, strategy, "", ())
        errors = checked.errors
    return RepairResult(reason="local_repair_failed", errors=errors)


def same_day_request(req, days, day, index, candidate_id, *, seconds, protected_ids=frozenset()):
    """Keep every identity; solve only this date. Other dates are merged unchanged."""
    original = next(d for d in days if d.date == day)
    ids = {v.poi_id for v in original.items}
    ids.remove(original.items[index].poi_id)
    ids.add(candidate_id)
    raw = req.model_dump(mode="json")
    raw.update(days=[d.model_dump(mode="json") for d in req.days if d.date == day],
               candidates=[{**p.model_dump(mode="json"), "allowed_dates": [day.isoformat()]}
                           for p in req.candidates if p.poi_id in ids],
               edges=[e.model_dump(mode="json") for e in req.edges if e.date == day],
               locked_items=[dict(poi_id=pid, date=day.isoformat()) for pid in sorted(ids)],
               preferred_start_minutes={candidate_id if v.poi_id == original.items[index].poi_id else v.poi_id: v.start_min
                                        for v in original.items},
               search_seconds=seconds, gap_target=None)
    fixed = {l.poi_id: l.start_min for l in req.locked_items if l.date == day}
    fixed.update({v.poi_id: v.start_min for v in original.items if v.poi_id in protected_ids})
    for lock in raw["locked_items"]:
        if lock["poi_id"] in fixed:
            lock["start_min"] = fixed[lock["poi_id"]]
    if raw["daily_composition"]:
        raw["daily_composition"]["meal_overrides"] = [o for o in raw["daily_composition"]["meal_overrides"] if o["date"] == day.isoformat()]
    return PlanRequest.model_validate(raw)
