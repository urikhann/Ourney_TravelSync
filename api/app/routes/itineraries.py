"""Build 3 candidate plans with the optimizer, list them, and vote."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import config, services
from ..db import get_db
from ..deps import member_trip
from ..models import Itinerary, PublicHoliday, RecommendationRun, Trip, User, Vote
from ..places import load_places
from ..scores import to_fit
from .auth import current_user

router = APIRouter(tags=["itineraries"])

VARIANTS = [("A", "best", "Best for everyone"), ("B", "culture", "Culture & views"), ("C", "food", "Food & cafés")]
NAMES = {v: n for _, v, n in VARIANTS}


def trip_dates(trip: Trip) -> list[str]:
    return [(trip.start_date + timedelta(d)).isoformat() for d in range((trip.end_date - trip.start_date).days + 1)]


def plans_view(trip: Trip, user: User, db: Session) -> dict:
    """Everything the Plan screen needs in one response."""
    its = db.scalars(select(Itinerary).where(Itinerary.trip_id == trip.id).order_by(Itinerary.letter)).all()
    votes = db.scalars(select(Vote).where(Vote.trip_id == trip.id)).all()
    run = db.get(RecommendationRun, its[0].run_id) if its else None
    ids = {i["poi_id"] for it in its for d in it.days for i in d["items"]}
    places = load_places(db, run.dataset_version, ids) if run else {}
    scale = run.response.get("score_scale", "0-1") if run else None
    fit = {r["poi_id"]: to_fit(r.get("mean_score"), scale) for r in (run.response.get("group", []) if run else [])}

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
    return {
        "plans": [{
            "id": it.id, "letter": it.letter, "variant": it.variant, "name": NAMES.get(it.variant, it.variant),
            "stats": it.stats, "votes": count[it.id],
            "days": [{"date": d["date"], "items": [{**i, "place": places.get(i["poi_id"]), "mean_score": fit.get(i["poi_id"])}
                                                   for i in d["items"]]} for d in it.days],
        } for it in its],
        "my_vote": my_vote, "voted": len(votes), "members": members, "chosen_id": chosen,
        "holidays": [{"date": h.day.isoformat(), "name": h.name} for h in holidays] if config.FEATURE_AVAILABILITY else [],
        "availability_checked": config.FEATURE_AVAILABILITY,
    }


@router.post("/trips/{trip_id}/itineraries/generate")
async def generate(trip: Trip = Depends(member_trip), user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.scalar(select(RecommendationRun).where(RecommendationRun.trip_id == trip.id)
                    .order_by(RecommendationRun.id.desc()))
    if not run:
        raise HTTPException(409, "no_recommendations_yet")
    scale = run.response.get("score_scale", "0-1")
    req = {"trip_id": trip.id, "dates": trip_dates(trip), "day_window": {"start": "10:00", "end": "21:00"},
           "dataset_version": run.dataset_version,
           "candidates": [{"poi_id": r["poi_id"], "group_rank": r["group_rank"], "group_utility": r["group_utility"],
                           "mean_score": r["mean_score"], "fit": to_fit(r["mean_score"], scale)}
                          for r in run.response.get("group", [])],
           "locked_items": [], "variants": [v for _, v, _ in VARIANTS],
           "check_availability": config.FEATURE_AVAILABILITY}
    res = await services.build_plans(req)
    # New plans replace the old ones (and their votes).
    db.execute(delete(Vote).where(Vote.trip_id == trip.id))
    db.execute(delete(Itinerary).where(Itinerary.trip_id == trip.id))
    by_variant = {p["variant"]: p for p in res.get("plans", [])}
    for letter, variant, _ in VARIANTS:
        if variant in by_variant:
            p = by_variant[variant]
            db.add(Itinerary(trip_id=trip.id, run_id=run.id, letter=letter, variant=variant, days=p["days"], stats=p["stats"]))
    db.commit()
    return plans_view(trip, user, db)


@router.get("/trips/{trip_id}/itineraries")
def list_plans(trip: Trip = Depends(member_trip), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return plans_view(trip, user, db)


@router.post("/itineraries/{itinerary_id}/vote")
def vote(itinerary_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    it = db.get(Itinerary, itinerary_id)
    trip = db.get(Trip, it.trip_id) if it else None
    if not it or not any(m.user_id == user.id for m in trip.members):
        raise HTTPException(404, "plan_not_found")
    v = db.scalar(select(Vote).where(Vote.trip_id == trip.id, Vote.user_id == user.id))
    if v:
        v.itinerary_id = it.id
    else:
        db.add(Vote(trip_id=trip.id, user_id=user.id, itinerary_id=it.id))
    db.commit()
    return plans_view(trip, user, db)
