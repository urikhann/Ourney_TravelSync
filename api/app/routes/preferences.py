"""Ten-feature taste survey and my personal "never take me there" rules.

Saved preferences (issue #21)
  GET  /me/preferences                         my saved 10 ratings (+ version history)
  PUT  /me/preferences                         save new defaults (version + 1); trips are NOT changed
  GET  /trips/{id}/preferences/me              this trip's ratings + "source": saved | custom | none
  PUT  /trips/{id}/preferences/me              customize for this trip; {"save_as_default": true} also saves them as my defaults
  POST /trips/{id}/preferences/me/use-saved    reset this trip to my saved ratings
  - Creating or joining a trip copies my saved ratings into it, so returning users skip the survey.
  - My first answer anywhere (trip or profile) becomes my saved preferences.
  - Every save is a new version, so recommendation runs keep the exact snapshot they used.

Validation and storage rules:
- exactly the 10 features, each a whole number 1–5. 0, 6, 4.5, "4" or a missing feature is refused (422).
- the raw 1–5 scores are stored as they are; the recommender derives weights w = r / sum(r) itself.
- saving never overwrites: it stores version + 1. Send expected_version to avoid overwriting a newer save.
- personal exclusions are a separate input (member exclusions), not part of the taste scores.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictInt, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import member_trip
from ..features import EXCLUSION_TAGS, FEATURES, LABELS
from ..models import Preference, Trip, TripMember, User, UserPreference
from .auth import current_user

router = APIRouter(tags=["preferences"])
DEFAULT_RATINGS = {f: 3 for f in FEATURES}


class PreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ratings: dict[str, StrictInt]          # StrictInt: 4.0, 4.5, "4" and true are refused
    expected_version: int | None = None
    save_as_default: bool = False      # trip save only: also make these my saved preferences

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


def saved(db: Session, user_id: str) -> UserPreference | None:
    return db.scalar(select(UserPreference).where(UserPreference.user_id == user_id).order_by(UserPreference.version.desc()))


def save_default(db: Session, user_id: str, ratings: dict) -> UserPreference:
    cur = saved(db, user_id)
    row = UserPreference(user_id=user_id, version=(cur.version if cur else 0) + 1, ratings={f: ratings[f] for f in FEATURES})
    db.add(row)
    return row


def apply_saved(db: Session, trip_id: str, user_id: str) -> bool:
    """Copy my saved ratings into a trip as its next version. False if I have none saved."""
    mine = saved(db, user_id)
    if not mine:
        return False
    cur = latest(db, trip_id, user_id)
    db.add(Preference(trip_id=trip_id, user_id=user_id, version=(cur.version if cur else 0) + 1,
                      ratings={f: mine.ratings[f] for f in FEATURES}))
    return True


def _same(a: dict, b: dict) -> bool:
    return all(a.get(f) == b.get(f) for f in FEATURES)


def _out(p: Preference | None, default: UserPreference | None = None) -> dict:
    source = "none" if not p else "saved" if default and _same(p.ratings, default.ratings) else "custom"
    base = {"source": source, "saved_version": default.version if default else 0}
    if not p:
        return {**base, "ratings": {f: default.ratings[f] for f in FEATURES} if default else DEFAULT_RATINGS,
                "version": 0, "submitted": False}
    return {**base, "ratings": {f: p.ratings[f] for f in FEATURES}, "version": p.version, "submitted": True}


def _profile(db: Session, user: User) -> dict:
    rows = db.scalars(select(UserPreference).where(UserPreference.user_id == user.id)
                      .order_by(UserPreference.version.desc())).all()
    if rows:
        return {"ratings": {f: rows[0].ratings[f] for f in FEATURES}, "version": rows[0].version, "saved": True,
                "history": [{"version": r.version, "saved_at": r.saved_at.isoformat()} for r in rows[:10]]}
    # never saved: start from my most recent trip answers, if any
    last = db.scalar(select(Preference).where(Preference.user_id == user.id).order_by(Preference.submitted_at.desc()))
    return {"ratings": {f: last.ratings[f] for f in FEATURES} if last else DEFAULT_RATINGS, "version": 0,
            "saved": False, "history": []}


def _member(db: Session, trip: Trip, user: User) -> TripMember:
    return db.scalar(select(TripMember).where(TripMember.trip_id == trip.id, TripMember.user_id == user.id))


@router.get("/meta/preferences")
def preference_options():
    """The survey items in the fixed order, with English and Korean labels, plus the exclusion choices."""
    return {"features": [{"code": f, "label": LABELS[f][0], "label_ko": LABELS[f][1]} for f in FEATURES],
            "scale": {"min": 1, "max": 5},
            "exclusion_tags": [{"code": k, "label": v} for k, v in EXCLUSION_TAGS.items()]}


@router.get("/me/preferences")
def get_saved(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _profile(db, user)


@router.put("/me/preferences")
def put_saved(body: PreferencesIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    cur = saved(db, user.id)
    if body.expected_version is not None and body.expected_version != (cur.version if cur else 0):
        raise HTTPException(409, f"preference_version_conflict: latest is {cur.version if cur else 0}")
    save_default(db, user.id, body.ratings)
    db.commit()
    return _profile(db, user)


@router.get("/trips/{trip_id}/preferences/me")
def get_mine(trip: Trip = Depends(member_trip), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(latest(db, trip.id, user.id), saved(db, user.id))


@router.post("/trips/{trip_id}/preferences/me/use-saved")
def use_saved(trip: Trip = Depends(member_trip), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not apply_saved(db, trip.id, user.id):
        raise HTTPException(409, "no_saved_preferences")
    db.commit()
    return _out(latest(db, trip.id, user.id), saved(db, user.id))


@router.put("/trips/{trip_id}/preferences/me")
def save_mine(body: PreferencesIn, trip: Trip = Depends(member_trip), user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    cur = latest(db, trip.id, user.id)
    cur_version = cur.version if cur else 0
    if body.expected_version is not None and body.expected_version != cur_version:
        raise HTTPException(409, f"preference_version_conflict: latest is {cur_version}")
    p = Preference(trip_id=trip.id, user_id=user.id, version=cur_version + 1, ratings=body.ratings)
    db.add(p)
    if body.save_as_default or not saved(db, user.id):  # my first answers become my saved preferences
        save_default(db, user.id, body.ratings)
    db.commit()
    return _out(p, saved(db, user.id))


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
