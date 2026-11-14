"""E2 after lazy enrichment. Never changes scores, GFAR ranks or utilities."""
from .contracts import Eligibility, Fact, PlanRequest, Window


def evidenced(f: Fact) -> bool:
    return f.value is not None and bool(f.source) and f.verified_at is not None


def usable_duration(f: Fact) -> bool:
    return f.known or (evidenced(f) and f.status == "ESTIMATED" and bool(f.rule_version))


def usable_cost(f: Fact) -> bool:
    return f.known or (evidenced(f) and f.status == "ESTIMATED" and f.user_upper_bound)


def common_bounds(req: PlanRequest) -> dict:
    """Fixed requested departure must be available to everyone.

    A route cannot cross an unavailable gap. Intersect the continuous member
    intervals containing departure; end no later than that common bound.
    The caller must request a new departure if the fixed start is unavailable.
    """
    out = {}
    for day in req.days:
        end = day.end_min
        for member in req.members:
            intervals = sorted(member.available.get(day.date, ()), key=lambda w: w.start_min)
            merged = []
            for w in intervals:
                if merged and w.start_min <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], w.end_min)
                else:
                    merged.append([w.start_min, w.end_min])
            containing = [b for a, b in merged if a <= day.start_min < b]
            if not containing:
                break
            end = min(end, max(containing))
        else:
            out[day.date] = Window(start_min=day.start_min, end_min=end)
    return out


def check_eligibility(req: PlanRequest) -> tuple[Eligibility, ...]:
    bounds = common_bounds(req)
    banned = {p for m in req.members for p in m.excluded_poi_ids}
    tags = {t for m in req.members for t in m.excluded_tags}
    access = {a for m in req.members for a in m.required_access}
    windows, reasons = {}, {}
    for p in req.candidates:
        why = []
        if p.poi_id in banned or tags.intersection(p.tags):
            why.append("EXPLICIT_EXCLUSION")
        if not usable_duration(p.duration_min):
            why.append("DURATION_UNKNOWN")
        if any(a not in p.access or not p.access[a].known or p.access[a].value is not True for a in access):
            why.append("ACCESS_UNVERIFIED_OR_UNSATISFIED")
        per_date = {}
        if not why:
            for day in req.days:
                op = p.operations.get(day.date)
                if p.allowed_dates is not None and day.date not in p.allowed_dates:
                    why.append(f"{day.date}:DAY_CANDIDATE_NOT_SELECTED")
                elif day.date not in bounds:
                    why.append(f"{day.date}:CONSTRAINT_CONFLICT")
                elif not req.enforce_opening_hours:
                    bound = bounds[day.date]
                    if p.is_festival and (op is None or not op.event_active.known or op.event_active.value is not True):
                        why.append(f"{day.date}:EVENT_UNVERIFIED_OR_INACTIVE")
                    elif bound.end_min - bound.start_min >= p.duration_min.value:
                        per_date[day.date] = (bound,)
                    else:
                        why.append(f"{day.date}:NO_FULL_STAY_WINDOW")
                elif op is None or not op.openings.known or not op.closed.known:
                    why.append(f"{day.date}:OPERATING_INFO_UNKNOWN")
                elif op.closed.value:
                    why.append(f"{day.date}:CLOSED")
                elif p.is_festival and (not op.event_active.known or op.event_active.value is not True):
                    why.append(f"{day.date}:EVENT_UNVERIFIED_OR_INACTIVE")
                else:
                    bound = bounds[day.date]
                    usable = tuple(Window(start_min=max(w.start_min, bound.start_min),
                                          end_min=min(w.end_min, bound.end_min))
                                   for w in op.openings.value
                                   if min(w.end_min, bound.end_min) - max(w.start_min, bound.start_min)
                                   >= p.duration_min.value)
                    if usable:
                        per_date[day.date] = usable
                    else:
                        why.append(f"{day.date}:NO_FULL_STAY_WINDOW")
        windows[p.poi_id], reasons[p.poi_id] = per_date, why
    # Reachability is necessary, not sufficient: the solver still checks time,
    # cost, order and return feasibility for the entire selected path.
    for day in req.days:
        nodes = {pid for pid, w in windows.items() if day.date in w}
        nodes.update((day.start_point, day.end_point))
        valid = [e for e in req.edges if e.date == day.date and e.usable
                 and e.origin in nodes and e.destination in nodes
                 and (req.max_leg_minutes is None or e.duration_minutes <= req.max_leg_minutes)]

        def reach(start, reverse=False):
            seen, todo = {start}, [start]
            while todo:
                at = todo.pop()
                for e in valid:
                    a, b = (e.destination, e.origin) if reverse else (e.origin, e.destination)
                    if a == at and b not in seen:
                        seen.add(b)
                        todo.append(b)
            return seen
        connected = reach(day.start_point) & reach(day.end_point, True)
        for pid in windows:
            if day.date in windows[pid] and pid not in connected:
                del windows[pid][day.date]
                reasons[pid].append(f"{day.date}:ROUTE_UNAVAILABLE")
    return tuple(Eligibility(poi_id=p.poi_id, schedule_eligible=bool(windows[p.poi_id]),
                             feasible_dates=tuple(sorted(windows[p.poi_id])), windows=windows[p.poi_id],
                             reasons=tuple(reasons[p.poi_id])) for p in req.candidates)
