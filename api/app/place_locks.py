"""Shared owner-only lock edits for the plan controls and confirmed chat tools."""
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select, update

from .lock_policy import describe, provenance
from .models import Itinerary, OptimizationRun, Trip
from . import replacements


def set_lock(db, trip, user_id, itinerary_id, item_id, locked, expected_revision):
    if trip.created_by != user_id:
        raise HTTPException(403, "owner_required")
    db.scalar(select(Trip).where(Trip.id == trip.id).with_for_update().execution_options(populate_existing=True))
    if db.bind.dialect.name == "sqlite":
        db.execute(update(Trip).where(Trip.id == trip.id).values(name=Trip.name))
    if trip.created_by != user_id:
        raise HTTPException(403, "owner_required")
    it = db.scalar(select(Itinerary).where(Itinerary.id == itinerary_id, Itinerary.trip_id == trip.id)
                   .with_for_update().execution_options(populate_existing=True))
    audit = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == itinerary_id, OptimizationRun.status == "PASSED")
                      .execution_options(populate_existing=True))
    if not it or not audit or it.variant != "optimized":
        raise HTTPException(404, "plan_not_found")
    if expected_revision != it.stats.get("lock_revision", 0):
        raise HTTPException(409, "place_lock_changed")
    now = replacements.current_time()
    protection = describe(it, audit, db, now).get(item_id)
    if protection is None:
        raise HTTPException(404, "plan_item_not_found")
    if protection["completed"] or protection["lock_reason"] == "confirmed_reservation":
        raise HTTPException(409, "place_lock_protected")
    if protection["locked"] == locked:
        return False
    explicit, temporary = provenance(db, audit)
    days = deepcopy(it.days)
    target_day, target = next((d, i) for d in days for i in d["items"] if f"{d['date']}@{i['start']}" == item_id)
    pid = target["poi_id"]
    if locked:
        explicit[pid] = "explicit_lock"
    else:
        explicit.pop(pid, None)
    temporary.discard(pid)
    target.update(locked=locked, lock_reason="explicit_lock" if locked else None)
    metadata = deepcopy(audit.input)
    metadata["lock_provenance"] = explicit
    metadata["temporary_lock_ids"] = sorted(temporary)
    locks = [l for l in metadata.get("configuration", {}).get("locked_items", []) if l["poi_id"] != pid]
    if locked:
        locks.append(dict(poi_id=pid, date=target_day["date"], start_min=target["start_min"]))
    metadata.setdefault("configuration", {})["locked_items"] = locks
    metadata.setdefault("lock_edits", []).append(dict(item_id=item_id, locked=locked, user_id=user_id, created_at=now.isoformat()))
    audit.input = metadata
    it.days = days
    it.stats = {**it.stats, "lock_revision": expected_revision + 1}
    db.commit()
    return True
