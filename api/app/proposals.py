"""Group voting over private E3-validated drafts. Approval rechecks and atomically
publishes the approved schedule; no solver or broader regeneration runs here.
Legacy suggestions without validated drafts cannot be applied.
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChatMessage, Itinerary, OptimizationRun, Poi, ProposalVote, Trip, Vote

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
    """The selected published version, or newest version before a selection exists."""
    audits = db.scalars(select(OptimizationRun).where(OptimizationRun.trip_id == trip_id, OptimizationRun.status == "PASSED",
                                                    OptimizationRun.itinerary_id.is_not(None))
                      .order_by(OptimizationRun.id.desc())).all()
    audit = audits[0] if audits else None
    if audit:
        by_plan = {a.itinerary_id: a for a in audits}
        ballots = db.scalars(select(Vote).where(Vote.trip_id == trip_id, Vote.itinerary_id.in_(by_plan))).all()
        if ballots:
            plans = [db.get(Itinerary, pid) for pid in by_plan]
            counts = {p.id: sum(v.itinerary_id == p.id for v in ballots) for p in plans}
            top = max(plans, key=lambda p: (counts[p.id], -ord(p.letter)))
            members = len(db.get(Trip, trip_id).members)
            if counts[top.id] > members/2 or len(ballots) == members:
                audit = by_plan[top.id]
    return (db.get(Itinerary, audit.itinerary_id), audit) if audit else (None, None)


def applied_changes(db: Session, trip_id: str, run_id: int) -> list[dict]:
    """Recent applied swaps in the current plan's ancestry, oldest first."""
    rows = db.scalars(select(ChatMessage.agent).where(
        ChatMessage.trip_id == trip_id, ChatMessage.kind == "agent",
        ChatMessage.agent["proposal_status"].as_string() == APPLIED,
    ).order_by(ChatMessage.id))
    by_result_run = {}
    for agent in rows:
        result = agent.get("result") or {}
        source, target = agent.get("plan_run_id"), result.get("plan_run_id")
        if result.get("status") == APPLIED and type(source) is int and type(target) is int and source < target:
            by_result_run[target] = agent

    changes = []
    while run_id in by_result_run and len(changes) < 5:
        agent = by_result_run[run_id]
        proposal, result = agent["proposal"], agent["result"]
        if agent.get("action_type", "replace_poi") != "replace_poi":
            changes.append(dict(action_type=agent["action_type"], affected_dates=agent.get("affected_dates", []),
                added=[dict(name=_name(db, a["poi_id"]), date=a.get("date"), start=a["start"]) for a in proposal.get("add", [])],
                removed=agent.get("removed", [])))
            run_id = agent["plan_run_id"]
            continue
        changes.append({
            "date": proposal["date"],
            "removed": [{"name": stop["name"], "start": stop["start"]} for stop in agent.get("removed", [])],
            "added": [{"name": _name(db, stop["poi_id"]), "start": result.get("start")} for stop in proposal["add"]],
            "day_rebuilt": result.get("mode") == "rebuild",
        })
        run_id = agent["plan_run_id"]
    return changes[::-1]


def tally(db: Session, message_id: int) -> list[ProposalVote]:
    return list(db.scalars(select(ProposalVote).where(ProposalVote.message_id == message_id)))


def agent_views(db: Session, trip: Trip, rows: list[ChatMessage], user_id: str) -> dict[int, dict]:
    """The `agent` field of each suggestion as the screen needs it: current status + vote counts."""
    ids = [m.id for m in rows if m.kind == "agent" and (m.agent or {}).get("proposal")]
    if not ids:
        return {}
    votes = list(db.scalars(select(ProposalVote).where(ProposalVote.message_id.in_(ids))))
    _, audit = published_plan(db, trip.id)
    source_ids = {m.agent.get("plan_run_id") for m in rows if m.id in ids}
    versions = {a.id: a.optimizer_request.get("dataset_version") for a in db.scalars(
        select(OptimizationRun).where(OptimizationRun.trip_id == trip.id, OptimizationRun.id.in_(source_ids)))}
    members = len(trip.members)
    out = {}
    for m in rows:
        if m.id not in ids:
            continue
        mine = [v for v in votes if v.message_id == m.id]
        status = m.agent.get("proposal_status", PENDING)
        if status == PENDING and (not m.agent.get("draft_id") or m.agent.get("plan_run_id") != (audit.id if audit else None)):
            status = STALE  # the plan changed (or this suggestion predates voting)
        out[m.id] = {**m.agent, "dataset_version": versions.get(m.agent.get("plan_run_id")), "proposal_status": status, "votes": {
            "approve": sum(v.approve for v in mine), "reject": sum(not v.approve for v in mine),
            "needed": needed(members), "members": members,
            "mine": next((v.approve for v in mine if v.user_id == user_id), None)}}
    return out


def _name(db: Session, poi_id: str) -> str:
    return db.scalar(select(Poi.name).where(Poi.id == poi_id).limit(1)) or poi_id


def removed_stops(db: Session, plan: Itinerary, proposal: dict) -> list[dict]:
    """Names of the stops a suggestion would remove, so the card can show "10:00 Busan Park → …"."""
    items = {f"{d['date']}@{i['start']}": i for d in plan.days for i in d["items"]}
    return [{"poi_id": items[x]["poi_id"], "start": items[x]["start"], "name": _name(db, items[x]["poi_id"])}
            for x in proposal.get("remove_item_ids") or [] if x in items]


async def apply(db: Session, trip: Trip, msg: ChatMessage) -> dict:
    """Apply exactly the validated draft; the caller owns the transaction."""
    from .replacements import apply_draft
    return await apply_draft(db, trip, msg)


def result_text(db: Session, msg: ChatMessage, result: dict, approve: int, members: int) -> str:
    p = msg.agent["proposal"]
    status = result["status"]
    if msg.agent.get("language") == "ko":
        if status == APPLIED:
            return "그룹이 승인한 검증된 변경안을 일정에 반영했어요. 일정 탭에서 확인해 주세요."
        if status == REJECTED:
            return "그룹이 변경안을 거절하여 기존 일정이 유지됩니다."
        from .replacements import explanation
        return explanation(result.get("reason", "stale_proposal"), True)
    if status == APPLIED:
        if msg.agent.get("action_type", "replace_poi") != "replace_poi":
            return "The group approved, so I updated the plan with the validated changes for the requested scope. Check the Plan tab."
        names = ", ".join(_name(db, a["poi_id"]) for a in p.get("add") or [])
        when = f" at {result['start']}" if result.get("start") else ""
        rest = ("Other activity times are unchanged." if result.get("strategy") == "fixed" else
                "The approved time changes are included; every other stop is retained.")
        return (f"✅ {approve} of {members} approved, so I updated the plan: {names} is now on {p['date']}{when}. "
                f"{rest} Check the Plan tab.")
    if status == REJECTED:
        return "The group rejected this suggestion, so the plan stays the same."
    if status == STALE:
        return "This suggestion is out of date because the plan changed. Ask me again for a new one."
    return ("The approved change could not be safely applied. Please request a new proposal. "
            "The plan is unchanged.")
