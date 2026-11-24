"""Group approval for TravelSync's suggested changes (issue #19).

A suggestion never touches the plan by itself:
  1. TravelSync posts a suggestion in the chat (status PENDING), tied to the plan it looked at
     (its optimizer run id, which is never reused).
  2. Members vote Approve / Reject. A majority of the trip's members decides.
  3. Approved -> the optimizer builds the plan again with the swap (new place locked on that day,
     removed place left out). It first tries to keep every other stop; if that can't fit, it lets the
     optimizer rearrange the rest. The result must pass the same E3 check as any plan.
     If neither works, the old plan stays and the chat says why.

Statuses: PENDING -> APPLYING -> APPLIED | FAILED, or PENDING -> REJECTED.
STALE: the plan changed after the suggestion was made, so it can no longer be applied.
"""
import copy
import logging

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChatMessage, Itinerary, OptimizationRun, Poi, ProposalVote, Trip, User
from .optimization import GenerateInput

PENDING, APPLYING, APPLIED, REJECTED, FAILED, STALE = "PENDING", "APPLYING", "APPLIED", "REJECTED", "FAILED", "STALE"
log = logging.getLogger("uvicorn.error")


def needed(members: int) -> int:
    """Approvals needed: more than half of the trip's members."""
    return members // 2 + 1


def decide(approve: int, reject: int, members: int) -> str | None:
    if approve >= needed(members):
        return "APPROVED"
    if members - reject < needed(members):  # not enough members left who could still approve
        return REJECTED
    return None


def published_plan(db: Session, trip_id: str) -> tuple[Itinerary | None, OptimizationRun | None]:
    """The plan the group currently sees (the latest optimizer run that passed E3 and was published)."""
    audit = db.scalar(select(OptimizationRun).where(OptimizationRun.trip_id == trip_id, OptimizationRun.status == "PASSED",
                                                    OptimizationRun.itinerary_id.is_not(None))
                      .order_by(OptimizationRun.id.desc()))
    return (db.get(Itinerary, audit.itinerary_id), audit) if audit else (None, None)


def tally(db: Session, message_id: int) -> list[ProposalVote]:
    return list(db.scalars(select(ProposalVote).where(ProposalVote.message_id == message_id)))


def agent_views(db: Session, trip: Trip, rows: list[ChatMessage], user_id: str) -> dict[int, dict]:
    """The `agent` field of each suggestion as the screen needs it: current status + vote counts."""
    ids = [m.id for m in rows if m.kind == "agent" and (m.agent or {}).get("proposal")]
    if not ids:
        return {}
    votes = list(db.scalars(select(ProposalVote).where(ProposalVote.message_id.in_(ids))))
    _, audit = published_plan(db, trip.id)
    members = len(trip.members)
    out = {}
    for m in rows:
        if m.id not in ids:
            continue
        mine = [v for v in votes if v.message_id == m.id]
        status = m.agent.get("proposal_status", PENDING)
        if status == PENDING and m.agent.get("plan_run_id") != (audit.id if audit else None):
            status = STALE  # the plan changed (or this suggestion predates voting)
        out[m.id] = {**m.agent, "proposal_status": status, "votes": {
            "approve": sum(v.approve for v in mine), "reject": sum(not v.approve for v in mine),
            "needed": needed(members), "members": members,
            "mine": next((v.approve for v in mine if v.user_id == user_id), None)}}
    return out


def _name(db: Session, poi_id: str) -> str:
    return db.scalar(select(Poi.name).where(Poi.id == poi_id).limit(1)) or poi_id


def _attempts(raw: dict, plan: Itinerary, proposal: dict, removed_ids: set, added: list) -> list[tuple[str, dict]]:
    """Plan configurations to try, best first.
    keep: every other stop stays on its day (times may shift to fit the new travel times).
    rebuild: only the new place is fixed; the optimizer may rearrange the rest of the trip."""
    base = {l["poi_id"]: l for l in raw.get("locked_items", []) if l["poi_id"] not in removed_ids}
    for pid in added:
        base[pid] = {"poi_id": pid, "date": proposal["date"]}
    keep = dict(base)
    for d in plan.days:
        for i in d["items"]:
            if i["poi_id"] not in removed_ids:
                keep.setdefault(i["poi_id"], {"poi_id": i["poi_id"], "date": d["date"]})
    excluded = sorted((set(raw.get("excluded_poi_ids") or []) | removed_ids) - set(added))
    return [(mode, {**raw, "locked_items": list(locks.values()), "excluded_poi_ids": excluded})
            for mode, locks in (("keep", keep), ("rebuild", base))]


# Optimizer answers that mean "this configuration doesn't fit", so a looser one is worth trying.
RETRYABLE = ("optimizer_search_timeout", "optimizer_no_feasible_itinerary", "optimizer_insufficient_candidates")


def removed_stops(db: Session, plan: Itinerary, proposal: dict) -> list[dict]:
    """Names of the stops a suggestion would remove, so the card can show "10:00 Busan Park → …"."""
    day = next((d for d in plan.days if d["date"] == proposal.get("date")), {"items": []})
    items = {f"{proposal['date']}@{i['start']}": i for i in day["items"]}
    return [{"poi_id": items[x]["poi_id"], "start": items[x]["start"], "name": _name(db, items[x]["poi_id"])}
            for x in proposal.get("remove_item_ids") or [] if x in items]


async def apply(db: Session, trip: Trip, msg: ChatMessage) -> dict:
    """Rebuild the published plan with the approved swap. Returns {status, reason?, start?, mode?}."""
    from .routes import itineraries  # late import: routes import this module

    proposal = msg.agent["proposal"]
    plan, audit = published_plan(db, trip.id)
    if not plan or audit.id != msg.agent.get("plan_run_id"):
        return {"status": STALE, "reason": "the plan changed after this suggestion"}
    day = next((d for d in plan.days if d["date"] == proposal["date"]), None)
    items = {f"{day['date']}@{i['start']}": i for i in day["items"]} if day else {}
    removed = [items.get(item_id) for item_id in proposal.get("remove_item_ids") or []]
    if not day or any(r is None or r.get("locked") for r in removed):
        return {"status": STALE, "reason": "the stop it wanted to change is no longer in the plan"}
    removed_ids = {r["poi_id"] for r in removed}
    added = [a["poi_id"] for a in proposal.get("add") or []]
    owner = db.get(User, trip.created_by)

    reason = "unexpected_error"
    for mode, raw in _attempts(copy.deepcopy(audit.input["configuration"]), plan, proposal, removed_ids, added):
        try:
            await itineraries._generate(GenerateInput.model_validate(raw), trip, owner, db)
        except HTTPException as e:
            reason = str(e.detail.get("code") if isinstance(e.detail, dict) else e.detail)
            if reason in RETRYABLE:
                continue
            return {"status": FAILED, "reason": reason}
        except ValidationError:
            return {"status": FAILED, "reason": "invalid_plan_configuration"}
        except Exception:  # a service crashed: keep the chat working and the old plan in place
            log.exception("applying proposal %s failed", msg.id)
            db.rollback()
            return {"status": FAILED, "reason": "unexpected_error"}
        new_plan, new_audit = published_plan(db, trip.id)
        start = next((i["start"] for d in new_plan.days if d["date"] == proposal["date"]
                      for i in d["items"] if i["poi_id"] in added), None)
        return {"status": APPLIED, "mode": mode, "start": start, "plan_run_id": new_audit.id}
    return {"status": FAILED, "reason": reason}


def result_text(db: Session, msg: ChatMessage, result: dict, approve: int, members: int) -> str:
    p = msg.agent["proposal"]
    status = result["status"]
    if status == APPLIED:
        names = ", ".join(_name(db, a["poi_id"]) for a in p.get("add") or [])
        when = f" at {result['start']}" if result.get("start") else ""
        rest = ("The other stops stay, though their times may shift a little to fit the new travel times."
                if result.get("mode") == "keep" else
                "To fit it in, I rebuilt the day, so some other stops changed too.")
        return (f"✅ {approve} of {members} approved, so I updated the plan: {names} is now on {p['date']}{when}. "
                f"{rest} Check the Plan tab.")
    if status == REJECTED:
        return "The group rejected this suggestion, so the plan stays the same."
    if status == STALE:
        return "This suggestion is out of date because the plan changed. Ask me again for a new one."
    return (f"The group approved, but I couldn't fit this change into a valid plan ({result.get('reason')}). "
            "The plan is unchanged.")
