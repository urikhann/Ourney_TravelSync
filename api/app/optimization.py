"""Production adapter: authenticated DB snapshots → planning data → Module H.

Only operational facts/routes come from the configured trusted adapter. It
cannot replace recommendation scores, member rules, dates or budget.
"""
import hashlib
import json
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ourney_travelsync.optimizer.contracts import (Candidate, Contract, DailyComposition, Edge, Fact, Lock, Member,
                                                  NonNegative, Operation, PlanRequest, Window)

from ourney_travelsync.planning_categories import visit_role

from . import config
from .models import DatasetVersion, MemberAvailability, Poi, Preference, RecommendationRun, Trip, TripMember


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Point(Contract):
    label: Annotated[str, Field(min_length=1, max_length=120)]
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]


class DailyInput(Window):
    date: date
    start_point: Point
    end_point: Point


class GenerateInput(Contract):
    daily_composition: DailyComposition = Field(default_factory=DailyComposition)
    days: Annotated[tuple[DailyInput, ...], Field(min_length=1, max_length=3)]
    budget_total: NonNegative
    transport_mode: Literal["walk", "drive", "transit"]
    max_leg_minutes: Annotated[int, Field(strict=True, ge=0, le=1440)] | None = None
    max_total_walk_minutes: Annotated[int, Field(strict=True, ge=0, le=1440)] | None = None
    required_access: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=60)], ...], Field(max_length=20)] = ()
    locked_items: Annotated[tuple[Lock, ...], Field(max_length=60)] = ()


class OperatingInput(Contract):
    poi_id: str
    duration_min: Fact[Annotated[int, Field(strict=True, ge=1, le=1440)]] = Field(default_factory=Fact)
    cost_per_person: Fact[NonNegative] = Field(default_factory=Fact)
    operations: dict[date, Operation] = Field(default_factory=dict)
    access: dict[str, Fact[bool]] = Field(default_factory=dict)


class PlanningSnapshot(Contract):
    schema_version: Literal["planning-data-1.0"]
    context_id: str
    dataset_version: str
    transport_mode: str
    operating_snapshot_id: Annotated[str, Field(min_length=1)]
    routing_version: Annotated[str, Field(min_length=1)]
    pois: Annotated[tuple[OperatingInput, ...], Field(max_length=60)]
    edges: Annotated[tuple[Edge, ...], Field(max_length=12000)]


def dates(trip: Trip):
    return [(trip.start_date + timedelta(days=i)).isoformat()
            for i in range((trip.end_date - trip.start_date).days + 1)]


def state(db: Session, trip_id: str) -> dict:
    """Read fresh values (not ORM identity-cache values) before/after HTTP calls."""
    first, last = db.execute(select(Trip.start_date, Trip.end_date).where(Trip.id == trip_id)).one()
    members = []
    for uid, role, exclusions in db.execute(select(TripMember.user_id, TripMember.role, TripMember.exclusions)
                                            .where(TripMember.trip_id == trip_id).order_by(TripMember.user_id)):
        pref = db.scalar(select(Preference.version).where(Preference.trip_id == trip_id, Preference.user_id == uid)
                         .order_by(Preference.version.desc()).limit(1))
        free = db.scalars(select(MemberAvailability.day).where(MemberAvailability.trip_id == trip_id,
                                                               MemberAvailability.user_id == uid)).all()
        members.append(dict(user_id=uid, role=role, exclusions=exclusions or {}, preference_version=pref,
                            dates=sorted(d.isoformat() for d in free)))
    latest = db.scalar(select(RecommendationRun.id).where(RecommendationRun.trip_id == trip_id)
                       .order_by(RecommendationRun.id.desc()).limit(1))
    return dict(start_date=first.isoformat(), end_date=last.isoformat(), members=members, recommendation_run_id=latest)


def prepare(trip: Trip, run: RecommendationRun, body: GenerateInput, db: Session, current: dict,
            recommendation=None, preflight_only=False):
    if run.method != "gfar":
        raise HTTPException(409, "optimizer_requires_gfar")
    if sorted(d.date.isoformat() for d in body.days) != dates(trip):
        raise HTTPException(422, "optimizer_dates_must_match_trip")
    version = db.get(DatasetVersion, run.dataset_version)
    if not version or version.feature_schema_version != run.feature_schema_version:
        raise HTTPException(409, "optimizer_dataset_mismatch")
    pinned = {m["user_id"]: m["preference_version"] for m in run.member_snapshot}
    if pinned != {m["user_id"]: m["preference_version"] for m in current["members"]}:
        raise HTTPException(409, "recommendations_stale")
    source_members = {m["user_id"]: m for m in run.request["members"]}
    for m in current["members"]:
        old = source_members.get(m["user_id"], {}).get("exclusions", {})
        if any(sorted(old.get(k, [])) != sorted(m["exclusions"].get(k, [])) for k in ("tags", "poi_ids")):
            raise HTTPException(409, "recommendations_stale")
        if any(d.date.isoformat() not in m["dates"] for d in body.days):
            raise HTTPException(409, "optimizer_member_unavailable")
    recommendation = run.response if recommendation is None else recommendation
    scale = recommendation.get("score_scale")
    if scale not in ("0-1", "1-5", "relative_relevance_0_1") or not recommendation.get("relevance_version"):
        raise HTTPException(409, "optimizer_recommendation_data_missing")
    group = recommendation.get("group", [])
    ids = {r["poi_id"] for r in group}
    if (len({l.poi_id for l in body.locked_items}) != len(body.locked_items)
            or any((not preflight_only and l.poi_id not in ids) or l.date.isoformat() not in dates(trip) for l in body.locked_items)):
        raise HTTPException(422, "optimizer_invalid_locks")
    if preflight_only and body.locked_items:
        locked_rows = db.scalars(select(Poi).where(Poi.dataset_version == run.dataset_version,
            Poi.id.in_([l.poi_id for l in body.locked_items]))).all()
        banned = {pid for m in current["members"] for pid in m["exclusions"].get("poi_ids", [])}
        banned_tags = {tag for m in current["members"] for tag in m["exclusions"].get("tags", [])}
        if (len(locked_rows) != len(body.locked_items)
                or any(p.id in banned or banned_tags.intersection(p.tags or []) for p in locked_rows)):
            raise HTTPException(422, "optimizer_invalid_locks")
    if preflight_only:
        return
    if not group or len(group) > 60 or len({r["poi_id"] for r in group}) != len(group):
        raise HTTPException(409, "optimizer_candidate_count_invalid")
    rows = {p.id: p for p in db.scalars(select(Poi).where(Poi.dataset_version == run.dataset_version,
                                                          Poi.id.in_([r["poi_id"] for r in group])))}
    if len(rows) != len(group):
        raise HTTPException(409, "optimizer_dataset_mismatch")
    members = tuple(Member(member_id=m["user_id"],
                           available={d.date: (Window(start_min=d.start_min, end_min=d.end_min),) for d in body.days},
                           excluded_poi_ids=m["exclusions"].get("poi_ids", ()),
                           excluded_tags=m["exclusions"].get("tags", ()), required_access=body.required_access)
                    for m in current["members"])
    candidates = []
    for row in group:
        raw_scores = row.get("member_scores", {})
        relevance = row.get("member_relevance", {})
        if set(raw_scores) != set(pinned) or set(relevance) != set(pinned):
            raise HTTPException(409, "optimizer_recommendation_data_missing")
        # Normalize the captured values without clamping invalid data into range.
        scores = {u: (v - 1) / 4 if scale == "1-5" else v for u, v in raw_scores.items()}
        p = rows[row["poi_id"]]
        role, category = visit_role(p.features)
        candidates.append(Candidate(poi_id=p.id, dataset_version=run.dataset_version,
                                    group_rank=row["group_rank"], group_utility=row["group_utility"],
                                    selection_gain=row.get("selection_gain"), mean_score=sum(scores.values()) / len(scores),
                                    personal_scores=scores, relevance=relevance, tags=tuple(p.tags),
                                    is_festival=bool(p.features.get("festival")),
                                    allowed_dates=row.get("candidate_dates"),
                                    visit_category=role, activity_category=category))
    daily = [dict(date=d.date.isoformat(), start_min=d.start_min, end_min=d.end_min,
                  start_point=f"start:{d.date}", end_point=f"end:{d.date}") for d in sorted(body.days, key=lambda d: d.date)]
    points = {f"{kind}:{d.date}": getattr(d, f"{kind}_point").model_dump(mode="json")
              for d in body.days for kind in ("start", "end")}
    return members, candidates, daily, points, rows, version.mapping_version


def catalogue_request(run, body, db):
    """Reuse pinned member preferences; retrieve all rows of the pinned dataset."""
    rows = db.scalars(select(Poi).where(Poi.dataset_version == run.dataset_version).order_by(Poi.id)).all()
    return {**run.request,
        "poi_snapshot": [dict(place_id=p.id, dataset_version=p.dataset_version, place_name=p.name,
            category_name=p.category, latitude=p.lat, longitude=p.lng,
            enriched_metadata=p.enriched_metadata, duration_min=p.duration_min,
            field_status=p.field_status, **p.features) for p in rows],
        "planning_days": [d.model_dump(mode="json") for d in body.days],
        "planning_locks": [l.model_dump(mode="json") for l in body.locked_items],
        "planning_composition": body.daily_composition.model_dump(mode="json")}


def data_request(run, audit_id, body, daily, points, rows, selection=None):
    context = dict(schema_version="planning-data-1.0", run_id=f"{run.id}:{audit_id}",
                   dataset_version=run.dataset_version, transport_mode=body.transport_mode,
                   group_size=len(run.member_snapshot), days=daily, points=points,
                   required_access=list(body.required_access),
                   pois=[dict(poi_id=p.id, latitude=p.lat, longitude=p.lng,
                              metadata=p.enriched_metadata, field_status=p.field_status)
                         for p in sorted(rows.values(), key=lambda p: p.id)])
    if selection is not None:
        context["day_candidate_ids"] = selection["day_candidate_ids"]
    return {**context, "context_id": fingerprint(context)}


def solver_request(run, audit_id, body, prepared, request, response):
    members, candidates, daily, _, _, mapping = prepared
    data = PlanningSnapshot.model_validate(response)
    if any(getattr(data, k) != request[k] for k in ("context_id", "dataset_version", "transport_mode")):
        raise ValueError("planning snapshot context mismatch")
    facts = {p.poi_id: p for p in data.pois}
    if len(facts) != len(data.pois) or set(facts) - {p.poi_id for p in candidates}:
        raise ValueError("unexpected planning POI")
    nodes = {p.poi_id for p in candidates} | set(request["points"])
    if any(e.origin not in nodes or e.destination not in nodes for e in data.edges):
        raise ValueError("unexpected route node")
    enriched = [Candidate.model_validate({**p.model_dump(mode="json"),
                 **(facts[p.poi_id].model_dump(mode="json", exclude={"poi_id"}) if p.poi_id in facts else {})})
                for p in candidates]
    # Reparse merged facts as nested typed objects; never trust model_copy alone.
    payload = dict(schema_version="optimizer-1.0", run_id=f"{run.id}:{audit_id}",
                   dataset_version=run.dataset_version, mapping_version=mapping,
                   feature_schema_version=run.feature_schema_version,
                   preference_version=fingerprint(run.member_snapshot), constraint_version=fingerprint(body.model_dump(mode="json")),
                   operating_snapshot_id=data.operating_snapshot_id, routing_version=data.routing_version,
                   transport_mode=body.transport_mode, days=daily, members=[m.model_dump(mode="json") for m in members],
                   candidates=[p.model_dump(mode="json") for p in enriched], edges=[e.model_dump(mode="json") for e in data.edges],
                   daily_composition=body.daily_composition.model_dump(mode="json"),
                   budget_total=body.budget_total, enforce_cost=False, enforce_opening_hours=False,
                   max_leg_minutes=body.max_leg_minutes,
                   max_total_walk_minutes=body.max_total_walk_minutes,
                   locked_items=[l.model_dump(mode="json") for l in body.locked_items],
                   search_seconds=config.OPTIMIZER_SEARCH_SECONDS, seed=int(run.response.get("tie_seed", 0)) % (2**31))
    return PlanRequest.model_validate(payload)
