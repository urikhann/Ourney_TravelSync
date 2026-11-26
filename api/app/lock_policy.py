"""Protection provenance. Votes are not locks; legacy retention needs proof."""
from zoneinfo import ZoneInfo
import copy

from sqlalchemy import select

from .models import ChatMessage, OptimizationRun


def provenance(db, audit, depth=0):
    if audit is None:
        return {}, set()
    if "lock_provenance" in audit.input:
        return dict(audit.input["lock_provenance"]), set(audit.input.get("temporary_lock_ids", []))
    normalize = lambda lock: {k: v for k, v in lock.items() if v is not None}
    configured = {l["poi_id"]: normalize(l) for l in audit.input.get("configuration", {}).get("locked_items", [])}
    if db is not None and depth < 20:
        messages = db.scalars(select(ChatMessage).where(ChatMessage.trip_id == audit.trip_id,
            ChatMessage.kind == "agent", ChatMessage.agent["proposal_status"].as_string() == "APPLIED")).all()
        for message in messages:
            agent = message.agent or {}
            if agent.get("draft_id") or (agent.get("result") or {}).get("plan_run_id") != audit.id:
                continue
            parent = db.get(OptimizationRun, agent.get("plan_run_id"))
            proposal = agent.get("proposal") or {}
            if not parent or not proposal.get("date"):
                continue
            original = {l["poi_id"]: normalize(l) for l in parent.input.get("configuration", {}).get("locked_items", [])}
            removed = {v["poi_id"] for d in parent.response.get("days", []) for v in d["items"]
                       if f"{d['date']}@{v['start_min']//60:02d}:{v['start_min']%60:02d}" in proposal.get("remove_item_ids", [])}
            expected = {pid: lock for pid, lock in original.items() if pid not in removed}
            expected.update({v["poi_id"]: dict(poi_id=v["poi_id"], date=proposal["date"]) for v in proposal.get("add", [])})
            if (agent.get("result") or {}).get("mode") == "keep":
                for d in parent.response.get("days", []):
                    for visit in d["items"]:
                        if visit["poi_id"] not in removed:
                            expected.setdefault(visit["poi_id"], dict(poi_id=visit["poi_id"], date=d["date"]))
            if expected == configured:
                explicit, _ = provenance(db, parent, depth+1)
                explicit = {pid: reason for pid, reason in explicit.items() if pid not in removed}
                return explicit, set(configured)-set(explicit)
    return {pid: "explicit_lock" for pid in configured}, set()


def describe(plan, audit, db, now):
    explicit, temporary = provenance(db, audit)
    now = now.astimezone(ZoneInfo("Asia/Seoul"))
    out = {}
    for day in plan.days:
        for item in day["items"]:
            reason = None
            completed = bool(item.get("completed")) or (day["date"], item["start"]) <= (now.date().isoformat(), now.strftime("%H:%M"))
            if item.get("reservation_confirmed") or item.get("lock_reason") == "confirmed_reservation":
                reason = "confirmed_reservation"
            elif item.get("lock_reason") == "explicit_lock":
                reason = "explicit_lock"
            elif item["poi_id"] in explicit:
                reason = explicit[item["poi_id"]]
            elif item.get("locked") and item["poi_id"] not in temporary:
                reason = "legacy_lock_unverified"  # never silently clear unexplained locks
            out[f"{day['date']}@{item['start']}"] = dict(locked=reason is not None,
                lock_reason=reason, completed=completed,
                protection_reason=reason or ("completed_activity" if completed else None))
    return out


def planning_configuration(plan, audit, latest, db, now):
    """Restore form settings while taking locks only from the selected saved plan.

    Failed/private solver attempts are diagnostics, not saved user lock choices.
    This changes form defaults only; incoming explicit locks are never stripped.
    """
    raw = (latest.input.get("configuration") if latest else None) or (audit.input.get("configuration") if audit else None)
    if raw is None:
        return None
    result = copy.deepcopy(raw)
    if plan is None or audit is None:
        return result
    info = describe(plan, audit, db, now)
    requested = {l["poi_id"]: l for l in audit.input.get("configuration", {}).get("locked_items", [])}
    locks = []
    for day in plan.days:
        for item in day["items"]:
            if info[f"{day['date']}@{item['start']}"]["locked"]:
                locks.append(copy.deepcopy(requested.get(item["poi_id"],
                    dict(poi_id=item["poi_id"], date=day["date"], start_min=item["start_min"]))))
    result["locked_items"] = locks
    return result
