"""Versioned, strict JSON contracts. Minutes are local to each calendar date.

The enrichment/routing adapters own evidence verification and expand holidays,
breaks, events and overnight hours into dated intervals before calling Module H.
"""
from datetime import date, datetime
from math import ceil, isfinite
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegative = Annotated[int, Field(strict=True, ge=0, le=10**9)]
Minute = Annotated[int, Field(strict=True, ge=0, le=1440)]
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
T = TypeVar("T")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fact(Contract, Generic[T]):
    value: T | None = None
    status: Literal["KNOWN", "ESTIMATED", "UNKNOWN"] = "UNKNOWN"
    source: str | None = None
    verified_at: datetime | None = None
    rule_version: str | None = None
    user_upper_bound: bool = False

    @property
    def known(self) -> bool:
        return (self.value is not None and self.status == "KNOWN"
                and bool(self.source) and self.verified_at is not None)


class Window(Contract):
    start_min: Minute
    end_min: Minute

    @model_validator(mode="after")
    def ordered(self):
        if self.start_min >= self.end_min:
            raise ValueError("window must have positive length")
        return self


class MealWindow(Window):
    name: Literal["breakfast", "lunch", "dinner"]


class DailyComposition(Contract):
    max_cafes: Annotated[int, Field(strict=True, ge=0, le=10)] = 2
    min_meals: Annotated[int, Field(strict=True, ge=0, le=3)] = 2
    max_meals: Annotated[int, Field(strict=True, ge=0, le=3)] = 3
    min_activities: Annotated[int, Field(strict=True, ge=0, le=10)] = 2
    min_activity_categories: Annotated[int, Field(strict=True, ge=0, le=9)] = 2
    separate_food_drink: bool = True
    meal_windows: tuple[MealWindow, ...] = (
        MealWindow(name="breakfast", start_min=420, end_min=660),
        MealWindow(name="lunch", start_min=660, end_min=900),
        MealWindow(name="dinner", start_min=1020, end_min=1260),
    )

    @model_validator(mode="after")
    def sensible(self):
        if self.min_meals > self.max_meals or self.min_meals > len(self.meal_windows):
            raise ValueError("invalid meal count bounds")
        if self.min_activity_categories > self.min_activities:
            raise ValueError("activity category minimum exceeds activity minimum")
        windows = sorted(self.meal_windows, key=lambda w: w.start_min)
        if len({w.name for w in windows}) != len(windows) or any(a.end_min > b.start_min for a,b in zip(windows,windows[1:])):
            raise ValueError("meal windows must be distinct and non-overlapping")
        return self


class Day(Window):
    date: date
    start_point: Annotated[str, Field(min_length=1)]
    end_point: Annotated[str, Field(min_length=1)]


class Operation(Contract):
    # A known empty interval list means closed. Unknown never means all-day open.
    openings: Fact[tuple[Window, ...]] = Field(default_factory=Fact)
    closed: Fact[bool] = Field(default_factory=Fact)
    event_active: Fact[bool] = Field(default_factory=Fact)


class Member(Contract):
    member_id: Annotated[str, Field(min_length=1)]
    available: dict[date, tuple[Window, ...]]
    excluded_poi_ids: tuple[str, ...] = ()
    excluded_tags: tuple[str, ...] = ()
    required_access: tuple[str, ...] = ()


class Candidate(Contract):
    poi_id: Annotated[str, Field(min_length=1)]
    dataset_version: Annotated[str, Field(min_length=1)]
    group_rank: Annotated[int, Field(strict=True, ge=1)]
    group_utility: Annotated[int, Field(strict=True, ge=0, le=1000)]
    selection_gain: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    mean_score: Unit
    personal_scores: dict[str, Unit]
    relevance: dict[str, Unit]
    visit_category: Literal["cafe", "meal", "activity"] = "activity"
    activity_category: Literal["nature", "history", "art", "shopping", "activity", "entertainment", "relax", "festival", "other"] = "other"
    allowed_dates: tuple[date, ...] | None = None
    tags: tuple[str, ...] = ()
    is_festival: bool = False
    duration_min: Fact[Annotated[int, Field(strict=True, ge=1, le=1440)]] = Field(default_factory=Fact)
    cost_per_person: Fact[NonNegative] = Field(default_factory=Fact)
    operations: dict[date, Operation] = Field(default_factory=dict)
    access: dict[str, Fact[bool]] = Field(default_factory=dict)


class Edge(Contract):
    date: date
    origin: Annotated[str, Field(min_length=1)]
    destination: Annotated[str, Field(min_length=1)]
    status: Literal["KNOWN", "UNKNOWN", "UNREACHABLE"]
    duration_minutes: Annotated[int, Field(strict=True, ge=0, le=1440)] | None = None
    distance_m: NonNegative | None = None
    group_fare: NonNegative | None = None
    walk_minutes: Annotated[int, Field(strict=True, ge=0, le=1440)] | None = None
    provider: str | None = None
    fetched_at: datetime | None = None

    @property
    def usable(self) -> bool:
        return (self.status == "KNOWN" and bool(self.provider) and self.fetched_at is not None
                and all(v is not None for v in (self.duration_minutes, self.distance_m,
                                               self.group_fare, self.walk_minutes)))


class Lock(Contract):
    poi_id: str
    date: date
    start_min: Minute | None = None


class PlanRequest(Contract):
    schema_version: Literal["optimizer-1.0"] = "optimizer-1.0"
    run_id: Annotated[str, Field(min_length=1)]
    dataset_version: Annotated[str, Field(min_length=1)]
    constraint_version: Annotated[str, Field(min_length=1)]
    preference_version: Annotated[str, Field(min_length=1)]
    mapping_version: Annotated[str, Field(min_length=1)]
    feature_schema_version: Literal["fs-1.0"]
    operating_snapshot_id: Annotated[str, Field(min_length=1)]
    routing_version: Annotated[str, Field(min_length=1)]
    transport_mode: Annotated[str, Field(min_length=1)]
    days: Annotated[tuple[Day, ...], Field(min_length=1, max_length=3)]
    members: Annotated[tuple[Member, ...], Field(min_length=1, max_length=10)]
    candidates: Annotated[tuple[Candidate, ...], Field(max_length=60)]
    edges: Annotated[tuple[Edge, ...], Field(max_length=12000)]
    budget_total: NonNegative
    daily_composition: DailyComposition | None = None
    enforce_cost: bool = True
    enforce_opening_hours: bool = True
    max_leg_minutes: Annotated[int, Field(strict=True, ge=0, le=1440)] | None = None
    max_total_walk_minutes: Annotated[int, Field(strict=True, ge=0, le=1440)] | None = None
    locked_items: tuple[Lock, ...] = ()
    search_seconds: Annotated[float, Field(ge=0, le=30, allow_inf_nan=False)] = 30
    seed: Annotated[int, Field(strict=True, ge=0, le=2**31-1)] = 0

    @model_validator(mode="after")
    def consistent(self):
        def unique(values, name):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {name}")
        unique([d.date for d in self.days], "date")
        unique([m.member_id for m in self.members], "member")
        unique([p.poi_id for p in self.candidates], "POI")
        unique([(e.date, e.origin, e.destination) for e in self.edges], "edge")
        unique([l.poi_id for l in self.locked_items], "lock")
        ids = {p.poi_id for p in self.candidates}
        dates = {d.date for d in self.days}
        members = {m.member_id for m in self.members}
        if any(d.start_point in ids or d.end_point in ids for d in self.days):
            raise ValueError("endpoint IDs must be separate from candidate IDs")
        for p in self.candidates:
            if p.allowed_dates is not None and not set(p.allowed_dates).issubset(dates):
                raise ValueError("candidate references unknown date")
            if p.dataset_version != self.dataset_version:
                raise ValueError("dataset_version mismatch")
            if set(p.personal_scores) != members or set(p.relevance) != members:
                raise ValueError("every member needs personal score and relevance; missing is not zero")
            if abs(sum(p.personal_scores.values()) / len(members) - p.mean_score) > 1e-6:
                raise ValueError("mean_score mismatch")
        if any(l.poi_id not in ids or l.date not in dates for l in self.locked_items):
            raise ValueError("lock references unknown POI/date")
        if any(e.date not in dates for e in self.edges):
            raise ValueError("edge references unknown date")
        return self


class Visit(Contract):
    poi_id: str
    arrival_min: Minute
    start_min: Minute
    departure_min: Minute
    travel_minutes: NonNegative
    stay_minutes: NonNegative
    visit_cost: NonNegative


class DayPlan(Contract):
    date: date
    start_point: str
    end_point: str
    start_min: Minute
    items: tuple[Visit, ...]
    end_arrival_min: Minute
    return_travel_minutes: NonNegative
    total_cost: NonNegative
    travel_minutes: NonNegative
    walk_minutes: NonNegative


class Eligibility(Contract):
    poi_id: str
    schedule_eligible: bool
    feasible_dates: tuple[date, ...]
    windows: dict[date, tuple[Window, ...]]
    reasons: tuple[str, ...]


class Phase(Contract):
    objective: Literal["group_utility", "minimum_relevance", "travel_minutes"]
    solver_status: str
    objective_value: float | None
    best_bound: float | None
    wall_seconds: float
    allocated_seconds: float


class Attempt(Contract):
    input_snapshot: PlanRequest
    solver_status: str
    validation_status: str
    error_code: str | None
    eligibility: tuple[Eligibility, ...]
    phases: tuple[Phase, ...]


class PlanResponse(Contract):
    schema_version: Literal["optimizer-1.0"] = "optimizer-1.0"
    run_id: str
    solver_status: Literal["NOT_RUN", "OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"]
    validation_status: Literal["NOT_RUN", "PASSED", "FAILED"] = "NOT_RUN"
    error_code: str | None = None
    eligibility: tuple[Eligibility, ...] = ()
    days: tuple[DayPlan, ...] = ()
    phases: tuple[Phase, ...] = ()
    attempts: tuple[Attempt, ...] = ()
    metrics: dict = Field(default_factory=dict)
    validation_errors: tuple[str, ...] = ()
    # Exact validated snapshot for replay; access-controlled, contains member data.
    input_snapshot: PlanRequest


class PlanRunRequest(Contract):
    """At most one already enriched GFAR expansion; never relax constraints."""
    schema_version: Literal["optimizer-run-1.0"] = "optimizer-run-1.0"
    initial: PlanRequest
    expanded: PlanRequest | None = None

    @model_validator(mode="after")
    def same_run(self):
        if self.expanded is not None:
            mutable = {"candidates", "edges", "operating_snapshot_id"}
            if self.initial.model_dump(exclude=mutable) != self.expanded.model_dump(exclude=mutable):
                raise ValueError("expansion must preserve run versions, members, constraints and total search cap")
            if len(self.expanded.candidates) <= len(self.initial.candidates):
                raise ValueError("expanded candidate pool must be larger (maximum 60)")
        return self


def ceil_minutes(value: float) -> int:
    """Routing/enrichment adapter helper; never round a positive duration down."""
    if isinstance(value, bool) or not isfinite(value) or value < 0:
        raise ValueError("duration must be finite and nonnegative")
    return ceil(value)
