"""E3: independent, objective-free reconstruction from the original snapshot.

Deliberately imports neither solver nor E2. No supplied eligibility flag,
objective, cached total, or solver status is accepted as evidence of feasibility.
"""
from dataclasses import dataclass, field

from pydantic import ValidationError

from .contracts import DayPlan, PlanRequest
from .meals import effective_meals, meals_can_be_assigned


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]
    metrics: dict = field(default_factory=dict)

    @property
    def passed(self):
        return not self.errors


def validate_final(req: PlanRequest, days: tuple[DayPlan, ...]) -> ValidationResult:
    try:
        # Revalidate even model_copy/model_construct results and nested mutations.
        req = PlanRequest.model_validate(req.model_dump(mode="json"))
        days = tuple(DayPlan.model_validate(d.model_dump(mode="json") if isinstance(d, DayPlan) else d)
                     for d in days)
    except (ValidationError, TypeError, AttributeError):
        return ValidationResult(("INVALID_VALIDATION_INPUT",))
    errors = []
    expected = {d.date: d for d in req.days}
    if len(days) != len(expected) or {d.date for d in days} != set(expected):
        errors.append("DATE_SET_MISMATCH")
    if len({d.date for d in days}) != len(days):
        errors.append("DUPLICATE_DATE")
    pois = {p.poi_id: p for p in req.candidates}
    edges = {(e.date, e.origin, e.destination): e for e in req.edges}
    seen, records = set(), {}
    total_cost, total_travel, total_walk = 0, 0, 0

    def problem(code, day, pid=""):
        errors.append(f"{day}:{pid}:{code}")

    def supported(f, kind):
        if f.value is None or not f.source or f.verified_at is None:
            return False
        return f.status == "KNOWN" or (f.status == "ESTIMATED" and
                (bool(f.rule_version) if kind == "duration" else f.user_upper_bound))

    for plan in days:
        day = expected.get(plan.date)
        if day is None:
            continue
        if (plan.start_point, plan.end_point, plan.start_min) != (day.start_point, day.end_point, day.start_min):
            problem("ENDPOINT_OR_DEPARTURE_MISMATCH", day.date)
        if not plan.items:
            problem("EMPTY_DAY", day.date)
        prev, depart, cost, travel, walk = day.start_point, day.start_min, 0, 0, 0

        def leg(destination):
            nonlocal cost, travel, walk
            e = edges.get((day.date, prev, destination))
            if e is None or e.status != "KNOWN" or not e.provider or e.fetched_at is None or any(
                    v is None for v in (e.duration_minutes, e.group_fare, e.walk_minutes, e.distance_m)):
                problem("ROUTE_UNVERIFIED", day.date, destination)
                return None
            if req.max_leg_minutes is not None and e.duration_minutes > req.max_leg_minutes:
                problem("MAX_LEG_EXCEEDED", day.date, destination)
            cost += e.group_fare
            travel += e.duration_minutes
            walk += e.walk_minutes
            return e

        for v in plan.items:
            if v.poi_id in seen:
                problem("DUPLICATE_POI", day.date, v.poi_id)
            seen.add(v.poi_id)
            records[v.poi_id] = (day.date, v.start_min)
            p = pois.get(v.poi_id)
            if p is None:
                problem("UNKNOWN_POI", day.date, v.poi_id)
                continue
            if p.allowed_dates is not None and day.date not in p.allowed_dates:
                problem("DAY_CANDIDATE_NOT_SELECTED", day.date, v.poi_id)
            e = leg(v.poi_id)
            if e is not None and (v.arrival_min != depart + e.duration_minutes or v.travel_minutes != e.duration_minutes):
                problem("TRAVEL_CONNECTION", day.date, v.poi_id)
            if not supported(p.duration_min, "duration"):
                problem("DURATION_UNVERIFIED", day.date, v.poi_id)
            # Unverified place fees are excluded from accounting, not verified as free.
            stay = p.duration_min.value
            fee = p.cost_per_person.value if supported(p.cost_per_person, "cost") else 0
            if stay is not None and (v.stay_minutes != stay or v.departure_min != v.start_min + stay):
                problem("STAY_MISMATCH", day.date, v.poi_id)
            if fee is not None:
                visit_cost = len(req.members) * fee
                cost += visit_cost
                if v.visit_cost != visit_cost:
                    problem("VISIT_COST_MISMATCH", day.date, v.poi_id)
            if v.start_min < v.arrival_min or v.start_min < day.start_min or v.departure_min > day.end_min:
                problem("VISIT_TIME_ORDER", day.date, v.poi_id)
            op = p.operations.get(day.date)
            if req.enforce_opening_hours and (op is None or not op.openings.known or not op.closed.known or op.closed.value is not False
                    or not any(w.start_min <= v.start_min and v.departure_min <= w.end_min
                               for w in (op.openings.value or ()))):
                problem("OPERATING_WINDOW", day.date, v.poi_id)
            if p.is_festival and (op is None or not op.event_active.known or op.event_active.value is not True):
                problem("EVENT_INVALID", day.date, v.poi_id)
            for m in req.members:
                if p.poi_id in m.excluded_poi_ids or set(m.excluded_tags).intersection(p.tags):
                    problem("EXPLICIT_EXCLUSION", day.date, v.poi_id)
                if any(a not in p.access or not p.access[a].known or p.access[a].value is not True
                       for a in m.required_access):
                    problem("REQUIRED_ACCESS", day.date, v.poi_id)
            prev, depart = v.poi_id, v.departure_min
        composition = req.daily_composition
        if composition is not None:
            actual = [(v, pois[v.poi_id]) for v in plan.items if v.poi_id in pois]
            cafes = [v for v,p in actual if p.visit_category == "cafe"]
            meals = [v for v,p in actual if p.visit_category == "meal"]
            activities = [p for v,p in actual if p.visit_category == "activity"]
            if len(cafes) > composition.max_cafes:
                problem("CAFE_LIMIT", day.date)
            if len(meals) > composition.max_meals:
                problem("MEAL_COUNT", day.date)
            if len(activities) < composition.min_activities:
                problem("ACTIVITY_COUNT", day.date)
            if len({p.activity_category for p in activities}) < composition.min_activity_categories:
                problem("ACTIVITY_CATEGORY_COUNT", day.date)
            effective = effective_meals(day, composition)
            if not meals_can_be_assigned(effective, meals):
                problem("REQUIRED_MEAL_MISSING_OR_CONFLICT", day.date)
            # All selected meal visits must fit an effective slot; matching of
            # required meals above uses distinct visits even if overrides overlap.
            for visit in meals:
                if not any(w.matches(visit.start_min, visit.departure_min) for w in effective):
                    problem("MEAL_WINDOW", day.date, visit.poi_id)
            if composition.separate_food_drink and any(
                    p.visit_category in ("meal", "cafe") and next_p.visit_category in ("meal", "cafe")
                    for (_,p),(_,next_p) in zip(actual,actual[1:])):
                problem("CONSECUTIVE_FOOD_DRINK", day.date)
        e = leg(day.end_point)
        if e is not None and (plan.end_arrival_min != depart + e.duration_minutes
                              or plan.return_travel_minutes != e.duration_minutes):
            problem("RETURN_CONNECTION", day.date)
        if plan.end_arrival_min > day.end_min or plan.end_arrival_min < day.start_min:
            problem("END_TIME", day.date)
        # The entire route (including travel/waiting) must remain continuously
        # available to every member. Union overlapping input windows separately.
        for m in req.members:
            until = day.start_min
            for w in sorted(m.available.get(day.date, ()), key=lambda w: w.start_min):
                if w.start_min <= until and w.end_min >= day.start_min:
                    until = max(until, w.end_min)
            if until < plan.end_arrival_min:
                problem("MEMBER_AVAILABILITY", day.date, m.member_id)
        if req.max_total_walk_minutes is not None and walk > req.max_total_walk_minutes:
            problem("WALK_LIMIT", day.date)
        if (plan.total_cost, plan.travel_minutes, plan.walk_minutes) != (cost, travel, walk):
            problem("DAY_TOTAL_MISMATCH", day.date)
        total_cost += cost
        total_travel += travel
        total_walk += walk
    if req.enforce_cost and total_cost > req.budget_total:
        errors.append("TOTAL_BUDGET_EXCEEDED")
    for lock in req.locked_items:
        actual = records.get(lock.poi_id)
        if actual is None or actual[0] != lock.date or (lock.start_min is not None and actual[1] != lock.start_min):
            errors.append(f"{lock.poi_id}:LOCK_VIOLATION")
    if errors:
        return ValidationResult(tuple(errors))
    selected = [pois[pid] for pid in sorted(seen)]
    member_utility = {m.member_id: sum(p.relevance[m.member_id] for p in selected) for m in req.members}
    personal = {m.member_id: sum(p.personal_scores[m.member_id] for p in selected) for m in req.members}
    minimum, maximum = min(member_utility.values()), max(member_utility.values())
    return ValidationResult((), dict(total_cost=total_cost, travel_minutes=total_travel,
                                     walk_minutes=total_walk, visit_count=len(selected),
                                     group_utility=sum(p.group_utility for p in selected),
                                     per_member_utility=member_utility, per_member_personal_score=personal,
                                     minimum_member_utility=minimum, utility_disparity=maximum - minimum,
                                     mean_personal_fit=sum(p.mean_score for p in selected) / len(selected)))
