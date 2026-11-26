"""Trip group chat, with TravelSync (the AI agent) answering when someone asks it.

    GET  /trips/{trip_id}/chat?after=<message id>   messages (oldest first), only newer ones when `after` is given
    POST /trips/{trip_id}/chat                       {"text": "...", "trigger": "MESSAGE" | "MENTION" | "QUICK_ACTION"}
    POST /trips/{trip_id}/chat/{message_id}/vote     {"approve": true | false}  vote on a TravelSync suggestion

Ordinary messages are only shared with the group; they are never sent to the AI.
The agent is asked only when a message starts with "@TravelSync" (or the short "@ai" / "@ts") (MENTION)
or comes from a quick-action button. Short mentions are saved as "@TravelSync" so the history reads the same.
The agent is the Gemini service on port 8005 (contracts/agent.*.json).
A suggested change only reaches the plan after the group approves it (issue #19, see app/proposals.py).
"""
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .. import proposals, replacements, services
from ourney_travelsync.replacement_intent import extract_replacement
from ..db import get_db
from ..deps import member_trip
from ..models import ChatMessage, ProposalVote, Trip, User
from .auth import current_user

router = APIRouter(tags=["chat"])

AGENT_NAME = "TravelSync"
MENTION = re.compile(r"^\s*@(travelsync|ai|ts)(?=\s|$)", re.IGNORECASE)
PAGE = 200
CONTEXT = 10  # recent messages the agent may see (contract: recent_messages)


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    trigger: Literal["MESSAGE", "MENTION", "QUICK_ACTION"] = "MESSAGE"

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return MENTION.sub("@" + AGENT_NAME, v.strip(), count=1)  # "@ai hi" -> "@TravelSync hi"


def message_dict(m: ChatMessage, names: dict[str, str], views: dict[int, dict] | None = None) -> dict:
    return {"id": m.id, "kind": m.kind, "text": m.text, "reply_to": m.reply_to,
            "author": None if m.kind == "agent" else {"id": m.author_id, "name": names.get(m.author_id or "", "Former member")},
            "agent": (views or {}).get(m.id, m.agent), "created_at": m.created_at.isoformat()}


def messages_out(db: Session, trip: Trip, rows: list[ChatMessage], user: User) -> list[dict]:
    views = proposals.agent_views(db, trip, rows, user.id)
    names = _names(db, trip)
    return [message_dict(m, names, views) for m in rows]


def _names(db: Session, trip: Trip) -> dict[str, str]:
    return {mem.user_id: mem.user.name for mem in trip.members}


def wants_agent(body: ChatIn) -> bool:
    return body.trigger == "QUICK_ACTION" or bool(MENTION.match(body.text))


async def ask_agent(db: Session, trip: Trip, msg: ChatMessage) -> dict:
    """Send one request to the agent service. Returns the `agent` field saved on the reply message."""
    recent = db.scalars(select(ChatMessage).where(ChatMessage.trip_id == trip.id, ChatMessage.id < msg.id)
                        .order_by(ChatMessage.id.desc()).limit(CONTEXT)).all()
    plan, plan_run = proposals.published_plan(db, trip.id)
    payload = {
        "trip_id": trip.id,
        "itinerary_id": plan.id if plan else None,
        "applied_changes": proposals.applied_changes(db, trip.id, plan_run.id) if plan else [],
        "message": {"id": msg.id, "user_id": msg.author_id, "text": msg.text, "sent_at": msg.created_at.isoformat()},
        "recent_messages": [{"id": r.id, "user_id": r.author_id, "kind": r.kind, "text": r.text,
                             "sent_at": r.created_at.isoformat()} for r in reversed(recent)],
    }
    try:
        # Straightforward replacements need no model round trips. The same backend
        # gate validates every model-originated suggestion below.
        out = ({"reply": "", "proposal": None, "tool_calls": []} if extract_replacement(msg.text)
               else await services.agent_turn(payload))
    except HTTPException as e:  # e.detail: agent_unavailable (not running / too slow) or agent_error_500 (it crashed)
        return {"state": "FAILED", "reply": f"{AGENT_NAME} can't answer right now. Please try again in a moment.",
                "tool_calls": [], "proposal": None, "error": e.detail}
    answer = {"state": "NEEDS_CLARIFICATION" if out.get("needs_clarification") else "SUCCEEDED",
              "reply": out.get("reply") or "", "tool_calls": out.get("tool_calls") or [],
              "proposal": out.get("proposal")}
    answer = await replacements.validate_proposal(db, trip, msg, plan, plan_run, answer)
    if answer["proposal"] and plan:
        answer.update(proposal_status=proposals.PENDING, plan_run_id=plan_run.id,
                      removed=proposals.removed_stops(db, plan, answer["proposal"]))
    elif answer["proposal"]:
        answer["proposal"] = None
    return answer


@router.get("/trips/{trip_id}/chat")
def list_chat(after: int = 0, trip: Trip = Depends(member_trip), user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    q = select(ChatMessage).where(ChatMessage.trip_id == trip.id, ChatMessage.id > after)
    rows = db.scalars(q.order_by(ChatMessage.id.desc()).limit(PAGE)).all()[::-1]  # newest PAGE, oldest first
    return {"agent_name": AGENT_NAME, "messages": messages_out(db, trip, rows, user)}


@router.post("/trips/{trip_id}/chat", status_code=201)
async def post_chat(body: ChatIn, trip: Trip = Depends(member_trip), user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    mine = ChatMessage(trip_id=trip.id, author_id=user.id, kind="user", text=body.text,
                       created_at=datetime.now(timezone.utc))
    db.add(mine)
    db.commit()
    created = [mine]
    if wants_agent(body):
        answer = await ask_agent(db, trip, mine)
        reply = ChatMessage(trip_id=trip.id, author_id=None, kind="agent", text=answer["reply"], agent=answer,
                            reply_to=mine.id, created_at=datetime.now(timezone.utc))
        db.add(reply)
        db.commit()
        created.append(reply)
    return {"messages": messages_out(db, trip, created, user)}


class VoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


@router.post("/trips/{trip_id}/chat/{message_id}/vote")
async def vote_on_proposal(message_id: int, body: VoteIn, trip: Trip = Depends(member_trip),
                           user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip_id = trip.id
    try:
        return await _vote_on_proposal(message_id, body, trip, user, db)
    except SQLAlchemyError:
        db.rollback()
        proposals.log.error("proposal_commit_failed message=%s trip=%s", message_id, trip_id)
        raise HTTPException(503, {"code": "proposal_commit_failed",
                                 "message": "The change could not be saved. The original plan is unchanged."}) from None


async def _vote_on_proposal(message_id, body, trip, user, db):
    """A member approves or rejects a TravelSync suggestion. A majority of members decides (issue #19)."""
    # Serialize approval against normal generation and other proposals for this trip.
    db.scalar(select(Trip).where(Trip.id == trip.id).with_for_update())
    if db.bind.dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE; acquire its write reservation before reading votes.
        db.execute(update(Trip).where(Trip.id == trip.id).values(name=Trip.name))
    msg = db.scalar(select(ChatMessage).where(ChatMessage.id == message_id, ChatMessage.trip_id == trip.id)
                    .with_for_update())
    if not msg or msg.kind != "agent" or not (msg.agent or {}).get("proposal"):
        raise HTTPException(404, "proposal_not_found")
    status = proposals.agent_views(db, trip, [msg], user.id)[msg.id]["proposal_status"]
    if status == proposals.STALE and msg.agent.get("proposal_status", proposals.PENDING) == proposals.PENDING:
        msg.agent = {**msg.agent, "proposal_status": proposals.STALE}
        db.commit()
    if status == proposals.APPLIED:
        return {"messages": messages_out(db, trip, [msg], user)}
    if status != proposals.PENDING:
        raise HTTPException(409, {"code": "proposal_closed", "status": status})
    vote = db.scalar(select(ProposalVote).where(ProposalVote.message_id == msg.id, ProposalVote.user_id == user.id))
    if vote:
        vote.approve = body.approve  # members may change their mind while it is open
    else:
        db.add(ProposalVote(message_id=msg.id, user_id=user.id, approve=body.approve))
    db.flush()

    votes = proposals.tally(db, msg.id)
    approve, reject, members = sum(v.approve for v in votes), sum(not v.approve for v in votes), len(trip.members)
    decision = proposals.decide(approve, reject, members)
    created = []
    if decision:
        if decision == "APPROVED":
            msg.agent = {**msg.agent, "proposal_status": proposals.APPLYING}  # blocks a second apply
            result = await proposals.apply(db, trip, msg)
        else:
            result = {"status": proposals.REJECTED}
        proposals.log.info("replacement proposal=%s message=%s status=%s reason=%s", msg.agent.get("draft_id"),
                           message_id, result["status"], result.get("reason", ""))
        msg = db.get(ChatMessage, message_id)
        msg.agent = {**msg.agent, "proposal_status": result["status"], "result": result}
        text = proposals.result_text(db, msg, result, approve, members)
        note = ChatMessage(trip_id=trip.id, author_id=None, kind="agent", text=text, reply_to=msg.id,
                           agent={"state": "SUCCEEDED", "reply": text, "proposal": None, "event": "proposal_result",
                                  # lets the web app show this note in the reader's language
                                  "result": {**result, "approve": approve, "members": members,
                                             "date": msg.agent["proposal"].get("date"),
                                             "added": [proposals._name(db, a["poi_id"])
                                                       for a in msg.agent["proposal"].get("add") or []]},
                                  "tool_calls": [{"name": "group_vote",
                                                  "summary": f"Group vote: {approve} approve, {reject} reject"}]},
                           created_at=datetime.now(timezone.utc))
        db.add(note)
        db.commit()
        created.append(note)
    else:
        db.commit()
    return {"messages": messages_out(db, db.get(Trip, trip.id), [db.get(ChatMessage, message_id), *created], user)}
