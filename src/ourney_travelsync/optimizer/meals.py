"""Pure meal policy resolution, shared by CP-SAT, E3 and future agent tools.

All minutes are wall-clock minutes on the specified Asia/Seoul calendar date.
No operational facts, scores or persisted itinerary are modified here.
"""
from dataclasses import dataclass

from .contracts import DailyComposition, MealOverride, PlanRequest


@dataclass(frozen=True)
class EffectiveMeal:
    name: str
    start_min: int
    end_min: int
    required: bool
    preferred_start_min: int | None = None
    fixed_start_min: int | None = None

    def matches(self, start, end):
        return (self.start_min <= start < end <= self.end_min
                and (self.fixed_start_min is None or start == self.fixed_start_min))


def effective_meals(day, policy: DailyComposition) -> tuple[EffectiveMeal, ...]:
    overrides = {o.name: o for o in policy.meal_overrides if o.date == day.date}
    result = []
    for base in policy.meal_windows:
        override = overrides.get(base.name)
        start, end = base.start_min, base.end_min
        preferred = fixed = None
        explicit = False
        if override is not None:
            preferred, fixed = override.preferred_start_min, override.fixed_start_min
            target = fixed if fixed is not None else preferred
            if override.window is not None:
                start, end = override.window.start_min, override.window.end_min
            elif target is not None and not start <= target < end:
                # An out-of-window time translates this meal's window, preserving
                # its width. It does not alter any other meal or calendar date.
                start, end = target, min(1440, target + end - start)
            explicit = override.required or target is not None
        overlap = max(0, min(day.end_min, end) - max(day.start_min, start))
        result.append(EffectiveMeal(base.name, start, end,
                                    explicit or overlap >= policy.meal_overlap_min,
                                    preferred, fixed))
    return tuple(result)


def validate_meal_overrides(days, policy):
    by_date = {d.date: d for d in days}
    for override in policy.meal_overrides:
        day = by_date.get(override.date)
        if day is None:
            raise ValueError(f"meal override references unknown date: {override.date}")
        meal = next(m for m in effective_meals(day, policy) if m.name == override.name)
        target = meal.fixed_start_min if meal.fixed_start_min is not None else meal.preferred_start_min
        if target is not None and not (day.start_min <= target < day.end_min
                                      and meal.start_min <= target < meal.end_min):
            raise ValueError(f"{override.date}:{override.name}: meal start outside travel or effective meal window")
        if meal.required and max(day.start_min, meal.start_min) >= min(day.end_min, meal.end_min):
            raise ValueError(f"{override.date}:{override.name}: required meal has no travel overlap")


def update_meal_constraints(req: PlanRequest, change: MealOverride, *, constraint_version: str) -> PlanRequest:
    """Typed future tool: replace one day/meal override and return a new snapshot.

    Caller must authorize membership/approval and re-optimize with this snapshot.
    This function never writes a plan or mutates the original request.
    """
    if constraint_version == req.constraint_version:
        raise ValueError("a meal update requires a new constraint_version")
    policy = req.daily_composition or DailyComposition()
    raw = req.model_dump(mode="json")
    raw["constraint_version"] = constraint_version
    raw["daily_composition"] = policy.model_dump(mode="json")
    raw["daily_composition"]["meal_overrides"] = [
        o.model_dump(mode="json") for o in policy.meal_overrides
        if (o.date, o.name) != (change.date, change.name)
    ] + [change.model_dump(mode="json")]
    return PlanRequest.model_validate(raw)


def meals_can_be_assigned(meals, visits):
    """Independent bipartite matching: a scheduled visit can cover one meal only."""
    if len(visits) > len(meals):
        return False
    required = {i for i, m in enumerate(meals) if m.required}
    options = [tuple(i for i, m in enumerate(meals)
                     if m.matches(v.start_min, v.departure_min)) for v in visits]

    def assign(index, used):
        if index == len(options):
            return required.issubset(used)
        return any(assign(index + 1, used | {i}) for i in options[index] if i not in used)

    return assign(0, set())
