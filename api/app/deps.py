"""Shared helpers for routes."""
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Trip, TripMember, User
from .routes.auth import current_user


def member_trip(trip_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Trip:
    """The trip, only if the logged-in user is a member. Otherwise 404 (don't reveal it exists)."""
    trip = db.get(Trip, trip_id)
    if not trip or not db.scalar(select(TripMember).where(TripMember.trip_id == trip_id,
                                                          TripMember.user_id == user.id)):
        raise HTTPException(404, "trip_not_found")
    return trip
