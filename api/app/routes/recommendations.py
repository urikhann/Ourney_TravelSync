"""Run the group recommender for a trip and read the latest result.

Flow (TS SDD 1.1, chapters 5, 8, 9): pin the ACTIVE dataset version and each member's latest preference version,
send them to the recommender (contracts/recommend.request.json), store the run, return the result with place details.
The recommender computes P(u,i) = sum_k w(u,k)·x(i,k) (0–1), removes excluded places (E1), and picks the
group list with GFAR. "average" and "awm" are comparison methods only (SRS: Should).
"""
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, services
from ..db import get_db
from ..deps import member_trip
from ..features import FEATURE_SCHEMA_VERSION, FEATURES
from ..import_places import active_version
from ..models import Poi, RecommendationRun, Trip, User
from ..places import load_places
from .auth import current_user
from .preferences import latest as latest_pref

router = APIRouter(tags=["recommendations"])
SCHEMA = "rec-1.0"


class RunIn(BaseModel):
    method: Literal["gfar", "average", "awm"] = "gfar"
    awm_threshold: float | None = Field(default=None, ge=0, le=5)  # on the recommender's score scale, only for "awm"
    group_k: int | None = Field(default=None, ge=1, le=100)


def tag_index(db: Session, version: str) -> dict[str, list[str]]:
    """tag -> place ids of this dataset version (tags like "hiking" are derived from the category at import)."""
    index: dict[str, list[str]] = {}
    for pid, tags in db.execute(select(Poi.id, Poi.tags).where(Poi.dataset_version == version)):
        for t in tags or []:
            index.setdefault(t, []).append(pid)
    return index


def build_request(trip: Trip, db: Session, body: RunIn) -> tuple[dict, list[dict], str]:
    """Turn the database rows into the contract in contracts/recommend.request.json."""
    version = active_version(db)
    if not version:
        raise HTTPException(503, "no_active_dataset")
    tags = None  # loaded only if someone has a tag rule
    members, waiting = [], []
    for m in sorted(trip.members, key=lambda m: m.id or 0):
        p = latest_pref(db, trip.id, m.user_id)
        if not p:
            waiting.append(m.user.name)
            continue
        ex = m.exclusions or {}
        resolved: dict[str, str] = {}  # place id -> the rule that removes it, so the recommender needs no tag data
        if ex.get("tags"):
            tags = tags if tags is not None else tag_index(db, version)
            for t in ex["tags"]:
                for pid in tags.get(t, []):
                    resolved.setdefault(pid, t)
        members.append({"user_id": m.user_id, "preference_version": p.version,
                        "ratings": {f: p.ratings[f] for f in FEATURES},
                        "exclusions": {"tags": ex.get("tags", []), "poi_ids": ex.get("poi_ids", []),
                                       "resolved": resolved}})
    if waiting:
        raise HTTPException(409, "waiting_for_preferences: " + ", ".join(waiting))
    days = (trip.end_date - trip.start_date).days
    req = {
        "schema_version": SCHEMA, "trip_id": trip.id, "city": trip.city,
        "dataset_version": version, "feature_schema_version": FEATURE_SCHEMA_VERSION, "features": FEATURES,
        "dates": [(trip.start_date + timedelta(d)).isoformat() for d in range(days + 1)],
        "members": members,
        "options": {"method": body.method, "top_l": config.REC_TOP_L, "group_k": body.group_k or config.REC_GROUP_K,
                    "awm_threshold": body.awm_threshold if body.method == "awm" else None},
    }
    snapshot = [{"user_id": m["user_id"], "preference_version": m["preference_version"]} for m in members]
    return req, snapshot, version


def enrich(run: RecommendationRun, trip: Trip, db: Session) -> dict:
    """Add place details and member names so the web app can show them directly."""
    res = run.response
    group, removed = res.get("group", []), res.get("removed", [])
    places = load_places(db, run.dataset_version, [r["poi_id"] for r in group + removed])
    names = {m.user_id: m.user.name for m in trip.members}

    def named(r: dict) -> dict:  # "Fair pick for <user id>" -> "Fair pick for Sora"
        rs = [{**x, "text": f"Fair pick for {names.get(x['user_id'], 'a member')}"} if x.get("code") == "fair_pick" else x
              for x in r.get("reasons", [])]
        return {**r, "reasons": rs, "place": places.get(r["poi_id"])}

    return {
        "run_id": run.id, "created_at": run.created_at.isoformat(), "model_version": run.model_version,
        "method": run.method, "awm_threshold": run.misery_threshold, "dataset_version": run.dataset_version,
        "feature_schema_version": run.feature_schema_version, "members": names,
        "group": [named(r) for r in group],
        "removed": [{**r, "place": places.get(r["poi_id"])} for r in removed],
        "metrics": res.get("metrics", {}),
    }


@router.post("/trips/{trip_id}/recommendations")
async def run_recommender(body: RunIn | None = None, trip: Trip = Depends(member_trip),
                          user: User = Depends(current_user), db: Session = Depends(get_db)):
    body = body or RunIn()
    req, snapshot, version = build_request(trip, db, body)
    res = await services.recommend(req)
    if res.get("dataset_version") not in (None, version) or \
            res.get("feature_schema_version") not in (None, FEATURE_SCHEMA_VERSION):
        raise HTTPException(502, "recommender_version_mismatch")   # SRS 7: versions must match
    run = RecommendationRun(trip_id=trip.id, model_version=res.get("model_version", "unknown"), method=body.method,
                            misery_threshold=req["options"]["awm_threshold"], dataset_version=version,
                            feature_schema_version=FEATURE_SCHEMA_VERSION, member_snapshot=snapshot,
                            request=req, response=res)
    db.add(run)
    db.commit()
    return enrich(run, trip, db)


@router.get("/trips/{trip_id}/recommendations/latest")
def latest(trip: Trip = Depends(member_trip), db: Session = Depends(get_db)):
    run = db.scalar(select(RecommendationRun).where(RecommendationRun.trip_id == trip.id)
                    .order_by(RecommendationRun.id.desc()))
    if not run:
        raise HTTPException(404, "no_recommendations_yet")
    return enrich(run, trip, db)
