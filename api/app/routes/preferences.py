"""My taste survey (10 features, SRS FR-PREF-01) and my personal "never take me there" rules.

Rules from TS SRS/SDD 1.1:
- exactly the 10 features, each a whole number 1–5. 0, 6, 4.5, "4" or a missing feature is refused (422).
- the raw 1–5 scores are stored as they are; the recommender derives weights w = r / sum(r) itself.
- saving never overwrites: it stores version + 1. Send expected_version to avoid overwriting a newer save.
- personal exclusions are a separate input (SDD 6.6 member_exclusions), not part of the taste scores.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictInt, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import member_trip
from ..features import EXCLUSION_TAGS, FEATURES, LABELS
from ..models import Preference, Trip, TripMember, User
from .auth import current_user

router = APIRouter(tags=["preferences"])
DEFAULT_RATINGS = {f: 3 for f in FEATURES}


class PreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ratings: dict[str, StrictInt]          # StrictInt: 4.0, 4.5, "4" and true are refused
    expected_version: int | None = None

    @field_validator("ratings")
    @classmethod
    def check_ratings(cls, v):
        missing = [f for f in FEATURES if f not in v]
        extra = [k for k in v if k not in FEATURES]
        if missing or extra:
            raise ValueError(f"ratings must have exactly these 10 keys: {', '.join(FEATURES)}"
                             + (f" (missing: {', '.join(missing)})" if missing else "")
                             + (f" (unknown: {', '.join(extra)})" if extra else ""))
        bad = [k for k, x in v.items() if not 1 <= x <= 5]
        if bad:
            raise ValueError(f"each rating must be a whole number 1–5 (wrong: {', '.join(bad)})")
        return {f: v[f] for f in FEATURES}  # keep the fixed order


class ExclusionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] = []
    poi_ids: list[str] = []

    @field_validator("tags")
    @classmethod
    def check_tags(cls, v):
        bad = [t for t in v if t not in EXCLUSION_TAGS]
        if bad:
            raise ValueError(f"unknown exclusion tags {bad}; allowed: {list(EXCLUSION_TAGS)}")
        return sorted(set(v))


def latest(db: Session, trip_id: str, user_id: str) -> Preference | None:
    return db.scalar(select(Preference).where(Preference.trip_id == trip_id, Preference.user_id == user_id)
                     .order_by(Preference.version.desc()))


def _out(p: Preference | None) -> dict:
    if not p:
        return {"ratings": DEFAULT_RATINGS, "version": 0, "submitted": False}
    return {"ratings": {f: p.ratings[f] for f in FEATURES}, "version": p.version, "submitted": True}


def _member(db: Session, trip: Trip, user: User) -> TripMember:
    return db.scalar(select(TripMember).where(TripMember.trip_id == trip.id, TripMember.user_id == user.id))


@router.get("/meta/preferences")
def preference_options():
    """The survey items in the fixed order, with English and Korean labels, plus the exclusion choices."""
    return {"features": [{"code": f, "label": LABELS[f][0], "label_ko": LABELS[f][1]} for f in FEATURES],
            "scale": {"min": 1, "max": 5},
            "exclusion_tags": [{"code": k, "label": v} for k, v in EXCLUSION_TAGS.items()]}


@router.get("/trips/{trip_id}/preferences/me")
def get_mine(trip: Trip = Depends(member_trip), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(latest(db, trip.id, user.id))


@router.put("/trips/{trip_id}/preferences/me")
def save_mine(body: PreferencesIn, trip: Trip = Depends(member_trip), user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    cur = latest(db, trip.id, user.id)
    cur_version = cur.version if cur else 0
    if body.expected_version is not None and body.expected_version != cur_version:
        raise HTTPException(409, f"preference_version_conflict: latest is {cur_version}")
    p = Preference(trip_id=trip.id, user_id=user.id, version=cur_version + 1, ratings=body.ratings)
    db.add(p)
    db.commit()
    return _out(p)


@router.get("/trips/{trip_id}/exclusions/me")
def get_exclusions(trip: Trip = Depends(member_trip), user: User = Depends(current_user), db: Session = Depends(get_db)):
    ex = _member(db, trip, user).exclusions or {}
    return {"tags": ex.get("tags", []), "poi_ids": ex.get("poi_ids", [])}


@router.put("/trips/{trip_id}/exclusions/me")
def save_exclusions(body: ExclusionsIn, trip: Trip = Depends(member_trip), user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    m = _member(db, trip, user)
    m.exclusions = {"tags": body.tags, "poi_ids": sorted(set(body.poi_ids))}
    db.commit()
    return m.exclusions
