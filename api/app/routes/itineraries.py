"""Authenticated Module H generation, independent E3 publication, and approval."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .. import config, services
from ..db import get_db
from ..deps import member_trip
from ..models import Itinerary, OptimizationRun, PublicHoliday, RecommendationRun, Trip, User, Vote
from ..optimization import GenerateInput, catalogue_request, data_request, fingerprint, prepare, solver_request, state
from ..places import load_places
from ..scores import to_fit
from .auth import current_user
from ourney_travelsync.optimizer.contracts import Contract, DailyComposition, MealOverride, PlanResponse
from ourney_travelsync.optimizer.validation import validate_final

router = APIRouter(tags=["itineraries"])

NAMES = {"optimized": "Optimized itinerary"}


def trip_dates(trip: Trip) -> list[str]:
    return [(trip.start_date + timedelta(d)).isoformat() for d in range((trip.end_date - trip.start_date).days + 1)]


def plans_view(trip: Trip, user: User, db: Session) -> dict:
    """Everything the Plan screen needs in one response."""
    # Old greedy mock rows lack a private E3 publication audit and stay hidden.
    its = db.scalars(select(Itinerary).join(OptimizationRun, OptimizationRun.itinerary_id == Itinerary.id)
                     .where(Itinerary.trip_id == trip.id, OptimizationRun.status == "PASSED",
                            Itinerary.variant == "optimized").order_by(Itinerary.id)).all()
    votes = db.scalars(select(Vote).where(Vote.trip_id == trip.id)).all()
    votes = [v for v in votes if v.itinerary_id in {it.id for it in its}]
    saved = {a.itinerary_id: a for a in db.scalars(select(OptimizationRun).where(
        OptimizationRun.trip_id == trip.id, OptimizationRun.itinerary_id.in_([it.id for it in its])))}
    current = state(db, trip.id)
    current.pop("recommendation_run_id")  # an independent comparison run does not change member constraints
    run = db.get(RecommendationRun, its[0].run_id) if its else None
    ids = {i["poi_id"] for it in its for d in it.days for i in d["items"]}
    places = load_places(db, run.dataset_version, ids) if run else {}
    scale = run.response.get("score_scale", "0-1") if run else None
    fit = {r["poi_id"]: to_fit(r.get("mean_score"), scale) for r in (run.response.get("group", []) if run else [])}

    fit_by_plan = {it.id: {r["poi_id"]: to_fit(r.get("mean_score"), scale)
                          for r in saved[it.id].input.get("candidate_retrieval", {}).get("group", [])}
                   if saved[it.id].input.get("candidate_retrieval") else fit for it in its}
    count = {it.id: sum(1 for v in votes if v.itinerary_id == it.id) for it in its}
    members = len(trip.members)
    chosen = None
    if its and votes:
        top = max(its, key=lambda it: (count[it.id], -ord(it.letter)))
        if count[top.id] > members / 2 or len(votes) == members:  # majority, or everyone has voted
            chosen = top.id
    first, last = trip.start_date, trip.end_date
    holidays = db.scalars(select(PublicHoliday).where(PublicHoliday.day >= first, PublicHoliday.day <= last)).all()
    my_vote = next((v.itinerary_id for v in votes if v.user_id == user.id), None)
    latest = db.scalar(select(OptimizationRun).where(OptimizationRun.trip_id == trip.id)
                       .order_by(OptimizationRun.id.desc()))
    rec = db.scalar(select(RecommendationRun).where(RecommendationRun.trip_id == trip.id)
                    .order_by(RecommendationRun.id.desc()))
    point_ids = {r["poi_id"] for r in rec.response.get("group", [])} if rec else set()
    if latest and rec and latest.recommendation_run_id == rec.id:
        point_ids.update(r["poi_id"] for r in latest.input.get("candidate_retrieval", {}).get("group", []))
    points = load_places(db, rec.dataset_version, point_ids) if rec else {}
    return {
        "plans": [{
            "id": it.id, "letter": it.letter, "variant": it.variant, "name": NAMES.get(it.variant, it.variant),
            "stale": {k: v for k, v in saved[it.id].input["state"].items() if k != "recommendation_run_id"} != current,
            "stats": it.stats, "votes": count[it.id],
            "days": [{**d, "items": [{**i, "place": places.get(i["poi_id"]), "mean_score": fit_by_plan[it.id].get(i["poi_id"])}
                                                   for i in d["items"]]} for d in it.days],
        } for it in its],
        "my_vote": my_vote, "voted": len(votes), "members": members, "chosen_id": chosen,
        "holidays": [{"date": h.day.isoformat(), "name": h.name} for h in holidays] if config.FEATURE_AVAILABILITY else [],
        "availability_checked": bool(its),
        "setup": {"dates": trip_dates(trip), "default_composition": DailyComposition().model_dump(mode="json"), "is_owner": trip.created_by == user.id,
                  "planning_data_configured": bool(config.PLANNING_DATA_URL),
                  "recommendations_ready": bool(rec and rec.method == "gfar" and rec.response.get("relevance_version")),
                  "points": list(points.values())},
        "latest_optimization": ({"id": latest.id, "status": latest.status, "error_code": latest.error_code,
                                 "solver_status": latest.response.get("solver_status", "NOT_RUN"),
                                 "validation_status": latest.response.get("validation_status", "NOT_RUN"),
                                 "eligibility": latest.response.get("eligibility", []),
                                 "configuration": latest.input.get("configuration"),
                                 "candidate_selection": latest.input.get("candidate_retrieval", {}).get("planning_candidate_selection")}
                                if latest else None),
    }


@router.post("/trips/{trip_id}/itineraries/generate")
async def generate(body: GenerateInput, trip: Trip = Depends(member_trip), user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    return await _generate(body, trip, user, db)


class MealProposalInput(Contract):
    configuration: GenerateInput
    change: MealOverride


@router.post("/trips/{trip_id}/itineraries/meal-proposals")
async def propose_meal_change(body: MealProposalInput, trip: Trip = Depends(member_trip),
                              user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Future tool boundary: authorize, resolve, re-optimize and preview only."""
    if trip.created_by != user.id:
        raise HTTPException(403, "optimizer_owner_required")
    raw = body.configuration.model_dump(mode="json")
    changes = body.configuration.daily_composition.meal_overrides
    raw["daily_composition"]["meal_overrides"] = [o.model_dump(mode="json") for o in changes
        if (o.date, o.name) != (body.change.date, body.change.name)] + [body.change.model_dump(mode="json")]
    try:
        updated = GenerateInput.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    return await _generate(updated, trip, user, db, preview=True)


async def _generate(body, trip, user, db, *, preview=False):
    if trip.created_by != user.id:
        raise HTTPException(403, "optimizer_owner_required")
    run = db.scalar(select(RecommendationRun).where(RecommendationRun.trip_id == trip.id)
                    .order_by(RecommendationRun.id.desc()))
    if not run:
        raise HTTPException(409, "no_recommendations_yet")
    current = state(db, trip.id)
    try:
        prepare(trip, run, body, db, current, preflight_only=True)
    except (ValidationError, ValueError, TypeError, KeyError):
        raise HTTPException(409, "optimizer_recommendation_data_missing") from None
    audit = OptimizationRun(trip_id=trip.id, recommendation_run_id=run.id, requested_by=user.id,
                            input={"configuration": body.model_dump(mode="json"), "state": current})
    db.add(audit)
    db.commit()
    audit_id, trip_id, user_id, run_id = audit.id, trip.id, user.id, run.id
    try:
        retrieval_request = catalogue_request(run, body, db)
        retrieved = await services.planning_candidates(retrieval_request)
        if (retrieved.get("dataset_version") != run.dataset_version
                or retrieved.get("feature_schema_version") != run.feature_schema_version
                or retrieved.get("score_scale") != run.response.get("score_scale")
                or not retrieved.get("planning_candidate_selection")):
            raise HTTPException(502, "planning_candidates_invalid_response")
        audit.input = {**audit.input, "candidate_retrieval": retrieved}
        db.commit()
        if not retrieved.get("group"):
            raise HTTPException(409, "optimizer_insufficient_candidates")
        try:
            group_ids = {r["poi_id"] for r in retrieved["group"]}
            selected = retrieved["planning_candidate_selection"]["day_candidate_ids"]
            if (set(selected) != {d.date.isoformat() for d in body.days}
                    or set(pid for ids in selected.values() for pid in ids) != group_ids
                    or any(set(r.get("candidate_dates", [])) != {d for d, ids in selected.items() if r["poi_id"] in ids}
                           for r in retrieved["group"])):
                raise ValueError("invalid day candidate membership")
            prepared = prepare(trip, run, body, db, current, recommendation=retrieved)
        except (ValidationError, ValueError, TypeError, KeyError):
            raise HTTPException(502, "planning_candidates_invalid_response") from None
        context = data_request(run, audit_id, body, prepared[2], prepared[3], prepared[4],
                               retrieved["planning_candidate_selection"])
        audit.planning_data = {"request": context}
        db.commit()
        operational = await services.planning_data(context)
        audit.planning_data = {"request": context, "response": operational}
        try:
            req = solver_request(run, audit_id, body, prepared, context, operational)
        except (ValidationError, ValueError, TypeError, KeyError):
            raise HTTPException(502, "planning_data_invalid_response") from None
        audit.optimizer_request = req.model_dump(mode="json")
        db.commit()
        response = await services.build_plans(req.model_dump(mode="json"))
        audit.response = response
        try:
            result = PlanResponse.model_validate(response)
        except ValidationError:
            raise HTTPException(502, "optimizer_invalid_response") from None
        if result.input_snapshot != req or result.run_id != req.run_id:
            raise HTTPException(502, "optimizer_snapshot_mismatch")
        if result.validation_status != "PASSED" or result.solver_status not in ("OPTIMAL", "FEASIBLE") or result.error_code:
            code = "optimizer_" + (result.error_code or "invalid_result").lower()
            if preview:
                raise HTTPException(409, {"code": code, "conflicts": list(result.validation_errors),
                                          "message": "Meal proposal is infeasible; saved itinerary is unchanged."})
            raise HTTPException(409, code)
        checked = validate_final(req, result.days)  # independent backend recheck, even if service says PASSED
        if not checked.passed:
            raise HTTPException(502, "optimizer_final_validation_failed")
        # Do not publish an answer for preferences, exclusions, membership,
        # availability or a recommendation run changed while the solver ran.
        db.flush()
        db.expire_all()
        db.scalar(select(Trip).where(Trip.id == trip_id).with_for_update())
        if fingerprint(state(db, trip_id)) != fingerprint(current):
            raise HTTPException(409, "optimizer_inputs_changed")
        newest = db.scalar(select(OptimizationRun.id).where(OptimizationRun.trip_id == trip_id)
                           .order_by(OptimizationRun.id.desc()).limit(1))
        if newest != audit_id:
            raise HTTPException(409, "optimizer_inputs_changed")
        audit = db.get(OptimizationRun, audit_id)
        if preview:
            audit.status = "PREVIEWED"
            db.commit()
            return {"proposal_id": audit_id, "status": "AWAITING_APPROVAL",
                    "configuration": body.model_dump(mode="json"),
                    "constraint_version": req.constraint_version,
                    "days": [d.model_dump(mode="json") for d in result.days],
                    "metrics": checked.metrics, "validation_status": "PASSED",
                    "enforcement": {"budget": req.enforce_cost, "opening_hours": req.enforce_opening_hours}}
        # All validation occurs before any old itinerary or vote is removed.
        db.execute(update(OptimizationRun).where(OptimizationRun.trip_id == trip_id)
                   .values(itinerary_id=None))
        db.execute(delete(Vote).where(Vote.trip_id == trip_id))
        db.execute(delete(Itinerary).where(Itinerary.trip_id == trip_id))
        days = []
        locked = {l.poi_id for l in req.locked_items}
        candidate_by_id = {p.poi_id: p for p in req.candidates}
        for d in result.days:
            raw = d.model_dump(mode="json")
            raw.update(start_label=prepared[3][d.start_point]["label"], end_label=prepared[3][d.end_point]["label"],
                       start=hhmm(d.start_min), end_arrival=hhmm(d.end_arrival_min))
            raw["items"] = [{**v.model_dump(mode="json"), "start": hhmm(v.start_min), "end": hhmm(v.departure_min),
                             "arrival": hhmm(v.arrival_min), "locked": v.poi_id in locked, "moved_from": None,
                             "visit_category": candidate_by_id[v.poi_id].visit_category,
                             "activity_category": candidate_by_id[v.poi_id].activity_category,
                             "availability": {"available": True}} for v in d.items]
            days.append(raw)
        plan = Itinerary(trip_id=trip_id, run_id=run_id, letter="A", variant="optimized", days=days,
                         stats={"group_fit": (None if run.response.get("score_scale") == "relative_relevance_0_1"
                                              else checked.metrics["mean_personal_fit"]),
                                "total_cost": checked.metrics["total_cost"], "cost_unknown_count": 0,
                                "daily_composition": req.daily_composition.model_dump(mode="json") if req.daily_composition else None,
                                "budget_total": req.budget_total, "transport_mode": req.transport_mode,
                                "travel_minutes": checked.metrics["travel_minutes"],
                                "walking_km_per_day": None, "solver_status": result.solver_status,
                                "validation_status": "PASSED", "optimization_run_id": audit_id,
                                "dataset_version": req.dataset_version, "routing_version": req.routing_version,
                                "operating_snapshot_id": req.operating_snapshot_id,
                                "objective_values": [p.model_dump(mode="json") for p in result.phases]})
        db.add(plan)
        db.flush()
        audit.itinerary_id, audit.status = plan.id, "PASSED"
        db.commit()
        return plans_view(db.get(Trip, trip_id), db.get(User, user_id), db)
    except HTTPException as exc:
        # Keep both the previous published plan and this failed attempt's audit.
        audit = db.get(OptimizationRun, audit_id)
        audit.status, audit.error_code = "FAILED", str(exc.detail)
        db.commit()
        raise


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@router.get("/trips/{trip_id}/itineraries")
def list_plans(trip: Trip = Depends(member_trip), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return plans_view(trip, user, db)


@router.post("/itineraries/{itinerary_id}/vote")
def vote(itinerary_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    it = db.get(Itinerary, itinerary_id)
    trip = db.get(Trip, it.trip_id) if it else None
    if not it or not any(m.user_id == user.id for m in trip.members):
        raise HTTPException(404, "plan_not_found")
    published = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == it.id,
                                                        OptimizationRun.status == "PASSED"))
    if not published or it.variant != "optimized":
        raise HTTPException(409, "optimizer_unvalidated_plan")
    current = state(db, trip.id)
    current.pop("recommendation_run_id")
    if {k: v for k, v in published.input["state"].items() if k != "recommendation_run_id"} != current:
        raise HTTPException(409, "optimizer_inputs_changed")
    v = db.scalar(select(Vote).where(Vote.trip_id == trip.id, Vote.user_id == user.id))
    if v:
        v.itinerary_id = it.id
    else:
        db.add(Vote(trip_id=trip.id, user_id=user.id, itinerary_id=it.id))
    db.commit()
    return plans_view(trip, user, db)
