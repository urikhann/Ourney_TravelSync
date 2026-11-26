"""Persisted per-user confirmation questions, separate from proposal votes."""
from datetime import datetime, timedelta
from uuid import uuid4
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sqlalchemy import select, update

from . import config, planning_actions, planning_intents, proposals, replacements
from .lock_policy import describe
from .models import ChatMessage, Trip
from .optimization import fingerprint, state
from fastapi import HTTPException


class PendingOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action_type: Literal["replace_poi", "remove_poi", "add_poi", "replan_day", "replan_unlocked", "replan_all"]
    target_item_id: str | None = Field(default=None, max_length=80)
    target_name: str | None = Field(default=None, max_length=200)
    date: str | None = Field(default=None, max_length=10)
    constraints: dict = Field(default_factory=dict)


def korean(message):
    return any("가" <= ch <= "힣" for ch in message.text)


def permissions(trip, user_id):
    member = next((m for m in trip.members if m.user_id == user_id), None)
    return dict(user_id=user_id, role=member.role, can_prepare=True,
                can_replan=trip.created_by == user_id, can_unlock=False) if member else None


def question(db, trip, message, plan, audit, action, reply):
    """The pending envelope is created by the backend, never accepted from a model."""
    now = replacements.current_time()
    try:
        action = PendingOperation.model_validate(action).model_dump(exclude_none=True)
    except ValidationError:
        return planning_actions.failure("invalid_pending_action", korean(message))
    if planning_intents.unsupported_constraints(action):
        return planning_actions.failure("unsupported_replacement_constraints", korean(message))
    if not plan or not audit:
        return planning_actions.failure("target_poi_not_found", korean(message))
    if action["action_type"].startswith("replan_") and trip.created_by != message.author_id:
        return planning_actions.failure("planning_owner_required", korean(message))
    if action["action_type"] in {"replace_poi", "remove_poi"}:
        from .models import Poi
        rows = db.scalars(select(Poi).where(Poi.dataset_version == audit.optimizer_request["dataset_version"])).all()
        try:
            target = planning_actions.target_for(action, plan, rows)
        except HTTPException:
            return planning_actions.failure("target_poi_not_found", korean(message))
        if target["item"]["poi_id"] in replacements.protected(plan, audit, db):
            return planning_actions.failure("locked_activity_conflict", korean(message))
        action = {**action, "target_item_id": target["item_id"], "target_name": None}
    pending = dict(id=uuid4().hex, action_type=action["action_type"], action=action,
        target_itinerary_item_id=action.get("target_item_id"), group_id=trip.id,
        authorized_user_id=message.author_id, permissions=permissions(trip, message.author_id),
        itinerary_id=plan.id, itinerary_version=audit.id, itinerary_hash=fingerprint(plan.days),
        constraints=action.get("constraints", {}), state_hash=fingerprint(state(db, trip.id)),
        created_at=now.isoformat(), expires_at=(now+timedelta(seconds=config.PENDING_ACTION_VALID_SECONDS)).isoformat(),
        status="PENDING", preceding_user_message_id=message.id, question_message_id=None)
    if not isinstance(reply, str) or not reply.strip():
        label = {"replace_poi": "a replacement", "remove_poi": "removing that activity", "add_poi": "adding that place",
                 "replan_day": "replanning that date", "replan_unlocked": "replanning unlocked activities", "replan_all": "replanning the whole trip"}[action["action_type"]]
        reply = ("요청한 작업의 검증된 변경안을 준비할까요? 변경안 적용에는 별도의 그룹 투표가 필요해요." if korean(message) else
                 f"Shall I prepare a validated draft for {label}? Applying it requires a separate group vote.")
    return dict(state="NEEDS_CLARIFICATION", needs_clarification=True, reply=reply, proposal=None,
                tool_calls=[], pending_action=pending, action_type=action["action_type"], language="ko" if korean(message) else "en")


def own_questions(db, trip_id, user_id):
    # JSON state reuses existing chat persistence; no new database schema.
    return [m for m in db.scalars(select(ChatMessage).where(ChatMessage.trip_id == trip_id, ChatMessage.kind == "agent",
        ChatMessage.agent["pending_action"]["authorized_user_id"].as_string() == user_id).order_by(ChatMessage.id.desc()).limit(200))]


async def confirm(db, trip, message, reply_to=None):
    positive = planning_intents.confirmation(message.text)
    if positive is None:
        return None
    ko = korean(message)
    db.scalar(select(Trip).where(Trip.id == trip.id).with_for_update())
    if db.bind.dialect.name == "sqlite":
        db.execute(update(Trip).where(Trip.id == trip.id).values(name=Trip.name))
    questions = own_questions(db, trip.id, message.author_id)
    if reply_to:
        questions = [q for q in questions if q.id == reply_to]
    else:
        active = [q for q in questions if q.agent["pending_action"]["status"] == "PENDING"]
        if len(active) > 1:
            return planning_actions.failure("ambiguous_pending_action", ko)
        if active:
            questions = active
        else:
            questions = questions[:1]
    if not questions:
        return planning_actions.failure("no_pending_action", ko)
    q = db.scalar(select(ChatMessage).where(ChatMessage.id == questions[0].id).with_for_update().execution_options(populate_existing=True))
    pending = dict(q.agent["pending_action"])
    # A plain affirmative only answers the immediately preceding question to this user.
    if not reply_to:
        source_ids = select(ChatMessage.id).where(ChatMessage.trip_id == trip.id, ChatMessage.author_id == message.author_id)
        latest = db.scalar(select(ChatMessage.id).where(ChatMessage.trip_id == trip.id, ChatMessage.kind == "agent",
            ChatMessage.id < message.id, ChatMessage.reply_to.in_(source_ids)).order_by(ChatMessage.id.desc()).limit(1))
        if latest != q.id:
            return planning_actions.failure("no_pending_action", ko)
    if pending["status"] != "PENDING":
        return planning_actions.failure("pending_action_consumed", ko)
    reason = None
    plan, audit = proposals.published_plan(db, trip.id)
    if replacements.current_time() >= datetime.fromisoformat(pending["expires_at"]):
        reason = "pending_action_expired"
    elif (not audit or pending["itinerary_version"] != audit.id or pending["itinerary_id"] != plan.id
          or pending["itinerary_hash"] != fingerprint(plan.days) or pending["state_hash"] != fingerprint(state(db, trip.id))
          or pending["permissions"] != permissions(trip, message.author_id)):
        reason = "pending_action_stale"
    if reason:
        q.agent = {**q.agent, "pending_action": {**pending, "status": "EXPIRED" if reason.endswith("expired") else "STALE"}}
        db.commit()
        return planning_actions.failure(reason, ko)
    pending.update(status="CONFIRMED" if positive else "DECLINED", confirmation_message_id=message.id,
                   confirmed_at=replacements.current_time().isoformat())
    q.agent = {**q.agent, "pending_action": pending}
    db.commit()  # claim once before external work; a second Yes cannot execute it again
    if not positive:
        return dict(state="SUCCEEDED", reply="취소했어요. 기존 일정은 유지됩니다." if ko else "Cancelled. The current plan is unchanged.",
                    proposal=None, tool_calls=[], reason="pending_action_declined")
    answer = await planning_actions.execute(db, trip, message, plan, audit, pending["action"])
    q.agent = {**q.agent, "pending_action": {**pending, "status": "CONSUMED", "result_reason": answer.get("reason"),
                                           "draft_id": answer.get("draft_id")}}
    return answer


async def route(db, trip, message, plan, audit, reply_to=None):
    confirmed = await confirm(db, trip, message, reply_to)
    if confirmed is not None:
        return confirmed
    action = planning_intents.extract(message.text)
    if not action:
        import re
        if re.match(r"^(?:yes\b|no\b|네[\s,]|아니요[\s,])", planning_intents.clean(message.text), re.I) and own_questions(db, trip.id, message.author_id):
            return planning_actions.failure("ambiguous_pending_action", korean(message))
        return None
    if action["action_type"] == "replan_all" and plan and replacements.protected(plan, audit, db):
        info = describe(plan, audit, db, replacements.current_time())
        count = sum(bool(x["protection_reason"]) for x in info.values())
        if count == len(info):
            return planning_actions.failure("no_unlocked_activities", korean(message))
        reply = (f"보호된 일정 {count}개가 있어 전체 재계획으로 임의 변경할 수 없어요. 잠금·예약·완료 일정을 유지하고 나머지를 다시 계획할까요? 보호 항목을 바꾸려면 먼저 별도로 잠금·예약 변경 권한을 확인해야 해요." if korean(message) else
                 f"Full replanning conflicts with {count} protected activities. Shall I replan unlocked activities while preserving locks, reservations and completed visits? Changing protected activities requires a separate explicit authorization through the plan controls.")
        return question(db, trip, message, plan, audit, dict(action_type="replan_unlocked", constraints=action.get("constraints", {})), reply)
    return await planning_actions.execute(db, trip, message, plan, audit, action)
