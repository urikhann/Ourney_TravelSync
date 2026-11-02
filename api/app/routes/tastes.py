"""Onboarding survey ("travel tastes"): asked once per user after sign-up, editable later. See app/taste_catalog.py.

    GET  /meta/tastes   the questions and options (Korean + English labels)
    GET  /me/tastes     my answers + what they turn into (keywords, avoid_keywords, exclusion_tags)
    PUT  /me/tastes     save answers, or {"skipped": true} so the survey is not shown again

Avoid options linked to a hard rule (hiking, raw fish, bars) become that rule in my trips, but only in trips where
I have not set any rules myself. A new trip I create or join starts with them too (see default_exclusions).
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import TripMember, User, UserTaste
from ..taste_catalog import MAX_PICKS, QUESTIONS, TASTES_VERSION, check_answers, profile
from .auth import current_user

router = APIRouter(tags=["tastes"])


class TastesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, list[str]] = {}
    skipped: bool = False

    @field_validator("answers")
    @classmethod
    def check(cls, v):
        return check_answers(v)


def tastes_out(t: UserTaste | None) -> dict:
    answers = check_answers({}) if t is None else {**check_answers({}), **(t.answers or {})}
    return {"status": t.status if t else None, "version": TASTES_VERSION, "answers": answers,
            **profile(answers if t and t.status == "done" else {})}


def default_exclusions(db: Session, user_id: str) -> dict:
    """Starting rules for a new trip membership: the hard rules from my onboarding answers."""
    t = db.get(UserTaste, user_id)
    tags = profile(t.answers)["exclusion_tags"] if t and t.status == "done" else []
    return {"tags": tags, "poi_ids": []}


@router.get("/meta/tastes")
def meta_tastes():
    return {"version": TASTES_VERSION, "max_picks": MAX_PICKS, "questions": QUESTIONS}


@router.get("/me/tastes")
def get_tastes(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return tastes_out(db.get(UserTaste, user.id))


@router.put("/me/tastes")
def save_tastes(body: TastesIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(UserTaste, user.id) or UserTaste(user_id=user.id)
    if body.skipped:
        t.status = "skipped" if t.status != "done" else t.status  # skipping later never deletes real answers
        t.answers = t.answers or check_answers({})
    else:
        t.answers, t.status = body.answers, "done"
    t.version = TASTES_VERSION
    db.add(t)
    if t.status == "done":
        tags = profile(t.answers)["exclusion_tags"]
        for m in db.scalars(select(TripMember).where(TripMember.user_id == user.id)):
            ex = m.exclusions or {}
            if tags and not ex.get("tags") and not ex.get("poi_ids"):  # only trips where I set no rules myself
                m.exclusions = {"tags": tags, "poi_ids": []}
    db.commit()
    return tastes_out(t)
