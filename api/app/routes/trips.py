"""Trips, members, invite links and availability."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..deps import member_trip
from ..emailer import send_email
from ..models import MemberAvailability, Preference, Trip, TripMember, User
from .auth import current_user

router = APIRouter(tags=["trips"])


class TripIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    city: str = "busan"
    start_date: date
    end_date: date
    budget_min: int | None = Field(default=None, ge=0)
    budget_max: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def check_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days + 1 > config.MAX_TRIP_DAYS:
            raise ValueError(f"trips can be at most {config.MAX_TRIP_DAYS} days")
        return self


class AvailabilityIn(BaseModel):
    dates: list[date]


def trip_summary(trip: Trip, db: Session) -> dict:
    return {"id": trip.id, "name": trip.name, "city": trip.city, "start_date": trip.start_date.isoformat(),
            "end_date": trip.end_date.isoformat(), "member_count": len(trip.members)}


def trip_detail(trip: Trip, me: User, db: Session) -> dict:
    prefs = {p.user_id for p in db.scalars(select(Preference).where(Preference.trip_id == trip.id))}
    free: dict[str, list[str]] = {}
    for a in db.scalars(select(MemberAvailability).where(MemberAvailability.trip_id == trip.id)):
        free.setdefault(a.user_id, []).append(a.day.isoformat())
    members = [{"user_id": m.user_id, "name": m.user.name, "role": m.role, "is_me": m.user_id == me.id,
                "preferences_done": m.user_id in prefs, "free_dates": sorted(free.get(m.user_id, []))}
               for m in sorted(trip.members, key=lambda m: m.id or 0)]
    return {**trip_summary(trip, db), "budget_min": trip.budget_min, "budget_max": trip.budget_max,
            "invite_code": trip.invite_code, "invite_url": f"{config.WEB_URL}/join/{trip.invite_code}",
            "members": members,
            "setup": {"members": len(members), "preferences_done": sum(m["preferences_done"] for m in members),
                      "waiting_for": [m["name"] for m in members if not m["preferences_done"]]}}


@router.post("/trips", status_code=201)
def create_trip(body: TripIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = Trip(**body.model_dump(), created_by=user.id)
    trip.members.append(TripMember(user_id=user.id, role="owner"))
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip_detail(trip, user, db)


@router.get("/trips")
def my_trips(user: User = Depends(current_user), db: Session = Depends(get_db)):
    trips = db.scalars(select(Trip).join(TripMember).where(TripMember.user_id == user.id)
                       .order_by(Trip.start_date)).all()
    return [trip_summary(t, db) for t in trips]


@router.get("/trips/{trip_id}")
def get_trip(trip: Trip = Depends(member_trip), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return trip_detail(trip, user, db)


@router.get("/invites/{code}")
def preview_invite(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = db.scalar(select(Trip).where(Trip.invite_code == code.upper()))
    if not trip:
        raise HTTPException(404, "invite_not_found")
    return {**trip_summary(trip, db), "already_member": any(m.user_id == user.id for m in trip.members)}


@router.post("/trips/join/{code}")
def join_trip(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = db.scalar(select(Trip).where(Trip.invite_code == code.upper()))
    if not trip:
        raise HTTPException(404, "invite_not_found")
    if not any(m.user_id == user.id for m in trip.members):
        if len(trip.members) >= config.MAX_GROUP_MEMBERS:
            raise HTTPException(409, "trip_full")
        trip.members.append(TripMember(user_id=user.id))
        db.commit()
    return trip_detail(trip, user, db)


@router.put("/trips/{trip_id}/availability")
def set_availability(body: AvailabilityIn, trip: Trip = Depends(member_trip),
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(delete(MemberAvailability).where(MemberAvailability.trip_id == trip.id,
                                                MemberAvailability.user_id == user.id))
    for d in sorted(set(body.dates)):
        db.add(MemberAvailability(trip_id=trip.id, user_id=user.id, day=d))
    db.commit()
    return trip_detail(trip, user, db)


@router.post("/trips/{trip_id}/nudge")
def nudge(trip: Trip = Depends(member_trip), user: User = Depends(current_user), db: Session = Depends(get_db)):
    detail = trip_detail(trip, user, db)
    pending = [m for m in trip.members if m.user.name in detail["setup"]["waiting_for"] and m.user_id != user.id]
    for m in pending:
        send_email(m.user.email, f"{user.name} is waiting for your travel preferences",
                   f"Hi {m.user.name},\n\n{user.name} asked you to fill in your preferences for "
                   f"\"{trip.name}\" so TravelSync can plan the trip.\n\n{config.WEB_URL}/trips/{trip.id}/preferences")
    return {"nudged": [m.user.name for m in pending]}
