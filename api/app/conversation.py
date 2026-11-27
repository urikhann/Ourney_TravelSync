"""Persisted per-user confirmation questions, separate from proposal votes."""
from datetime import datetime, timedelta
from uuid import uuid4
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sqlalchemy import select, update

from . import config, planning_actions, planning_intents, proposals, replacements
from .lock_policy import describe
from .models import ChatMessage, Trip
from .chat_language import korean
from .optimization import fingerprint, state
from fastapi import HTTPException


class PendingOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action_type: Literal["replace_poi", "remove_poi", "add_poi", "replan_day", "replan_unlocked", "replan_all", "set_visit_duration", "set_place_lock"]
    target_item_id: str | None = Field(default=None, max_length=80)
    target_name: str | None = Field(default=None, max_length=200)
    date: str | None = Field(default=None, max_length=10)
    constraints: dict = Field(default_factory=dict)
    duration_min: int | None = Field(default=None, ge=1, le=1440)
    locked: bool | None = None

    @model_validator(mode="after")
    def settings_fields(self):
        if self.action_type == "set_visit_duration" and self.duration_min is None:
            raise ValueError("visit duration required")
        if self.action_type == "set_place_lock" and self.locked is None:
            raise ValueError("lock state required")
        if self.action_type != "set_visit_duration" and self.duration_min is not None:
            raise ValueError("unexpected visit duration")
        if self.action_type != "set_place_lock" and self.locked is not None:
            raise ValueError("unexpected lock state")
        return self


def permissions(trip, user_id):
    member = next((m for m in trip.members if m.user_id == user_id), None)
    return dict(user_id=user_id, role=member.role, can_prepare=True,
                can_replan=trip.created_by == user_id, can_unlock=trip.created_by == user_id) if member else None


def question(db, trip, message, plan, audit, action, reply, reply_localized=None):
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
    settings = action["action_type"] in {"set_visit_duration", "set_place_lock"}
    if settings and trip.created_by != message.author_id:
        return planning_actions.failure("settings_owner_required", korean(message))
    if action["action_type"].startswith("replan_") and trip.created_by != message.author_id:
        return planning_actions.failure("planning_owner_required", korean(message))
    if action["action_type"] in {"replace_poi", "remove_poi", "set_visit_duration", "set_place_lock"}:
        from .models import Poi
        rows = db.scalars(select(Poi).where(Poi.dataset_version == audit.optimizer_request["dataset_version"])).all()
        try:
            target = planning_actions.target_for(action, plan, rows)
        except HTTPException as exc:
            return planning_actions.failure(str(exc.detail), korean(message))
        if not settings and target["item"]["poi_id"] in replacements.protected(plan, audit, db):
            return planning_actions.failure("locked_activity_conflict", korean(message))
        if settings:
            protection = describe(plan, audit, db, now)[target["item_id"]]
            if protection["completed"] or protection["lock_reason"] == "confirmed_reservation":
                return planning_actions.failure("place_lock_protected", korean(message))
            name = next((p.name for p in rows if p.id == target["item"]["poi_id"]), target["item"]["poi_id"])
            if action["action_type"] == "set_place_lock":
                reply_localized = {"en": f"{'Lock' if action['locked'] else 'Unlock'} {name}? Its scheduled times will stay the same.",
                                   "ko": f"{name}의 잠금을 {'설정' if action['locked'] else '해제'}할까요? 현재 일정 시간은 유지돼요."}
            else:
                minutes = action["duration_min"]
                reply_localized = {"en": f"Set the visit to {name} to {minutes} minutes and prepare a validated schedule change? Applying it requires a group vote.",
                                   "ko": f"{name} 방문 시간을 {minutes}분으로 설정한 변경안을 검증할까요? 일정에 적용하려면 그룹 투표가 필요해요."}
            reply = reply_localized["ko" if korean(message) else "en"]
        action = {**action, "target_item_id": target["item_id"], "target_name": None}
    pending = dict(id=uuid4().hex, action_type=action["action_type"], action=action,
        target_itinerary_item_id=action.get("target_item_id"), group_id=trip.id,
        authorized_user_id=message.author_id, permissions=permissions(trip, message.author_id),
        itinerary_id=plan.id, itinerary_version=audit.id, itinerary_hash=fingerprint(plan.days),
        constraints=action.get("constraints", {}), state_hash=fingerprint(state(db, trip.id)),
        created_at=now.isoformat(), expires_at=(now+timedelta(seconds=config.PENDING_ACTION_VALID_SECONDS)).isoformat(),
        status="PENDING", preceding_user_message_id=message.id, question_message_id=None)
    if settings:
        pending["lock_revision"] = plan.stats.get("lock_revision", 0)
    if not isinstance(reply, str) or not reply.strip():
        label = {"replace_poi": "a replacement", "remove_poi": "removing that activity", "add_poi": "adding that place",
                 "replan_day": "replanning that date", "replan_unlocked": "replanning unlocked activities", "replan_all": "replanning the whole trip"}[action["action_type"]]
        reply_localized = {"ko": "요청한 작업의 검증된 변경안을 준비할까요? 변경안 적용에는 별도의 그룹 투표가 필요해요.",
                           "en": f"Shall I prepare a validated draft for {label}? Applying it requires a separate group vote."}
        reply = reply_localized["ko" if korean(message) else "en"]
    return dict(state="NEEDS_CLARIFICATION", needs_clarification=True, reply=reply, proposal=None,
                **({"reply_localized": reply_localized} if reply_localized else {}),
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
          or ("lock_revision" in pending and pending["lock_revision"] != plan.stats.get("lock_revision", 0))
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
        replies = {"ko": "취소했어요. 기존 일정은 유지됩니다.", "en": "Cancelled. The current plan is unchanged."}
        return dict(state="SUCCEEDED", reply=replies["ko" if ko else "en"], reply_localized=replies,
                    proposal=None, tool_calls=[], reason="pending_action_declined")
    answer = await planning_actions.execute(db, trip, message, plan, audit, pending["action"])
    q.agent = {**q.agent, "pending_action": {**pending, "status": "CONSUMED", "result_reason": answer.get("reason"),
                                           "draft_id": answer.get("draft_id")}}
    return answer


def date_question(answer, plan, audit, message, action):
    return {**answer,
        "language": "ko" if korean(message) else "en",
        "reply_localized": {"en": "Which date should I change? Choose a day below.", "ko": "변경할 날짜를 아래에서 선택해 주세요."},
        "reply": "변경할 날짜를 선택해 주세요." if korean(message) else "Which date should I change? Choose a day below.",
        "date_selection": dict(dates=sorted({d["date"] for d in plan.days}), action=action,
            authorized_user_id=message.author_id, itinerary_version=audit.id,
            itinerary_hash=fingerprint(plan.days), status="PENDING")}


async def select_date(db, trip, message, plan, audit, reply_to):
    import re
    question = db.scalar(select(ChatMessage).where(ChatMessage.id == reply_to,
        ChatMessage.trip_id == trip.id, ChatMessage.kind == "agent"))
    selection = (question.agent or {}).get("date_selection") if question else None
    if not selection:
        return None
    if getattr(message, "reply_language", None) is None:
        message.reply_language = (question.agent or {}).get("language") or ("ko" if korean(question) else "en")
    ko = korean(message)
    if selection["authorized_user_id"] != message.author_id:
        return planning_actions.failure("no_pending_action", ko)
    day = planning_intents.clean(message.text)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or day not in selection["dates"]:
        return planning_actions.failure("scope_date_required", ko)
    db.scalar(select(Trip).where(Trip.id == trip.id).with_for_update())
    if db.bind.dialect.name == "sqlite":
        db.execute(update(Trip).where(Trip.id == trip.id).values(name=Trip.name))
    db.refresh(question)
    selection = question.agent["date_selection"]
    if selection["status"] != "PENDING":
        return planning_actions.failure("pending_action_consumed", ko)
    plan, audit = proposals.published_plan(db, trip.id)
    if (not plan or not audit or selection["itinerary_version"] != audit.id
            or selection["itinerary_hash"] != fingerprint(plan.days)):
        return planning_actions.failure("pending_action_stale", ko)
    question.agent = {**question.agent, "date_selection": {**selection, "status": "CONSUMED", "date": day}}
    db.commit()
    return await planning_actions.execute(db, trip, message, plan, audit, {**selection["action"], "date": day})


async def route(db, trip, message, plan, audit, reply_to=None):
    if reply_to:
        selected = await select_date(db, trip, message, plan, audit, reply_to)
        if selected is not None:
            return selected
    confirmed = await confirm(db, trip, message, reply_to)
    if confirmed is not None:
        return confirmed
    action = planning_intents.extract(message.text)
    if not action:
        import re
        if re.match(r"^(?:yes\b|no\b|네[\s,]|아니요[\s,])", planning_intents.clean(message.text), re.I) and own_questions(db, trip.id, message.author_id):
            return planning_actions.failure("ambiguous_pending_action", korean(message))
        return None
    if action["action_type"] in {"set_visit_duration", "set_place_lock"}:
        return question(db, trip, message, plan, audit, action, "")
    if action["action_type"] == "replan_all" and plan and replacements.protected(plan, audit, db):
        info = describe(plan, audit, db, replacements.current_time())
        count = sum(bool(x["protection_reason"]) for x in info.values())
        if count == len(info):
            return planning_actions.failure("no_unlocked_activities", korean(message))
        replies = {"ko": f"보호된 일정 {count}개가 있어 전체 재계획으로 임의 변경할 수 없어요. 잠금·예약·완료 일정을 유지하고 나머지를 다시 계획할까요? 보호 항목을 바꾸려면 먼저 별도로 잠금·예약 변경 권한을 확인해야 해요.",
                   "en": f"Full replanning conflicts with {count} protected activities. Shall I replan unlocked activities while preserving locks, reservations and completed visits? Changing protected activities requires a separate explicit authorization through the plan controls."}
        return question(db, trip, message, plan, audit, dict(action_type="replan_unlocked", constraints=action.get("constraints", {})),
                        replies["ko" if korean(message) else "en"], replies)
    return await planning_actions.execute(db, trip, message, plan, audit, action)
