"""Confirmed single-place settings through existing lock and validation gates."""
import asyncio
import copy

from fastapi import HTTPException
from sqlalchemy import select

from ourney_travelsync.optimizer.validation import validate_final
from . import config, planning_actions, replacements
from .chat_language import korean
from .lock_policy import describe
from .models import Poi
from .optimization import fingerprint, state
from .place_locks import set_lock


async def execute(db, trip, message, plan, audit, action):
    ko = korean(message)
    if trip.created_by != message.author_id:
        return planning_actions.failure("settings_owner_required", ko)
    if not plan or not audit:
        return planning_actions.failure("no_itinerary_yet", ko)
    before, base_hash = state(db, trip.id), fingerprint(plan.days)
    if before != audit.input["state"]:
        return planning_actions.failure("stale_proposal", ko)
    rows = db.scalars(select(Poi).where(Poi.dataset_version == audit.optimizer_request["dataset_version"])).all()
    try:
        target = planning_actions.target_for(action, plan, rows)
        protection = describe(plan, audit, db, replacements.current_time())[target["item_id"]]
        if protection["completed"] or protection["lock_reason"] == "confirmed_reservation":
            raise HTTPException(409, "place_lock_protected")
        pid = target["item"]["poi_id"]
        name = next((p.name for p in rows if p.id == pid), pid)
        if action["action_type"] == "set_place_lock":
            set_lock(db, trip, message.author_id, plan.id, target["item_id"], action["locked"], plan.stats.get("lock_revision", 0))
            replies = {"en": f"{name} is now {'locked' if action['locked'] else 'unlocked'}. Its scheduled times are unchanged.",
                       "ko": f"{name}의 잠금을 {'설정' if action['locked'] else '해제'}했어요. 현재 일정 시간은 그대로예요."}
            return dict(state="SUCCEEDED", reply=replies["ko" if ko else "en"], reply_localized=replies,
                        proposal=None, tool_calls=[], action_type="set_place_lock", event="planning_settings_updated")
        minutes = action["duration_min"]
        if target["item"]["stay_minutes"] == minutes:
            replies = {"en": f"The visit to {name} is already {minutes} minutes. The plan is unchanged.",
                       "ko": f"{name} 방문 시간은 이미 {minutes}분이에요. 일정은 그대로예요."}
            return dict(state="SUCCEEDED", reply=replies["ko" if ko else "en"], reply_localized=replies, proposal=None, tool_calls=[])
        req, protected = planning_actions.normalized_request(plan, audit, db)
        original = replacements.plans(plan.days)
        retained = {i["poi_id"] for d in plan.days if d["date"] == target["date"] for i in d["items"]}
        async with asyncio.timeout(config.PLANNING_ACTION_SECONDS):
            req, days, evidence, affected = await planning_actions.scoped_solve(db, trip, message, plan, audit,
                req, original, {**action, "date": target["date"]}, protected, rows,
                retained_ids=retained, duration_overrides={pid: minutes})
            checked = validate_final(req, days)
            if not checked.passed:
                return planning_actions.failure("final_validation_failed", ko, details=list(checked.errors))
            configuration = copy.deepcopy(audit.input["configuration"])
            configuration["duration_overrides"] = {**configuration.get("duration_overrides", {}), pid: minutes}
            draft = replacements.persist_draft(db, trip, message, plan, audit, req, days, configuration, before,
                dict(base_plan_hash=base_hash, action_type="set_visit_duration", strategy="duration",
                     target_item_id=target["item_id"], affected_dates=sorted(affected), protected_ids=sorted(protected)), evidence)
        old = {(d.date, v.poi_id): v for d in original for v in d.items}
        changes = [dict(poi_id=v.poi_id, date=d.date.isoformat(), start_min=v.start_min, departure_min=v.departure_min)
                   for d in days for v in d.items if old.get((d.date, v.poi_id)) != v]
        replies = {"en": f"I validated a plan with {minutes} minutes at {name}, keeping all existing places. Review the timing changes and vote to apply them.",
                   "ko": f"기존 장소를 모두 유지하면서 {name}에서 {minutes}분 머무는 변경안을 검증했어요. 시간 변경을 확인하고 적용 여부에 투표해 주세요."}
        return dict(state="SUCCEEDED", reply=replies["ko" if ko else "en"], reply_localized=replies,
                    proposal=dict(date=target["date"], remove_item_ids=[], add=[]), action_type="set_visit_duration",
                    affected_dates=sorted(affected), affected_activities=changes, draft_id=draft.id,
                    validation_status="PASSED", repair_strategy="duration", language="ko" if ko else "en", tool_calls=[])
    except TimeoutError:
        return planning_actions.failure("planning_action_timeout", ko)
    except HTTPException as exc:
        return planning_actions.failure(str(exc.detail), ko)
