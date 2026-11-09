"""Trip group chat, with TravelSync (the AI agent) answering when someone asks it.

    GET  /trips/{trip_id}/chat?after=<message id>   messages (oldest first), only newer ones when `after` is given
    POST /trips/{trip_id}/chat                       {"text": "...", "trigger": "MESSAGE" | "MENTION" | "QUICK_ACTION"}

Ordinary messages are only shared with the group; they are never sent to the AI.
The agent is asked only when a message starts with "@TravelSync" (MENTION) or comes from a quick-action button.
Today the agent is the mock on port 8001 (contracts/agent.*.json). When the real agent is ready, only
`ask_agent` below (and AGENT_URL) change — the chat screen and this API stay the same.
"""
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..db import get_db
from ..deps import member_trip
from ..models import ChatMessage, Itinerary, Trip, User
from .auth import current_user

router = APIRouter(tags=["chat"])

AGENT_NAME = "TravelSync"
MENTION = re.compile(r"^\s*@travelsync(?=\s|$)", re.IGNORECASE)
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
        return v.strip()


def message_dict(m: ChatMessage, names: dict[str, str]) -> dict:
    return {"id": m.id, "kind": m.kind, "text": m.text, "reply_to": m.reply_to,
            "author": None if m.kind == "agent" else {"id": m.author_id, "name": names.get(m.author_id or "", "Former member")},
            "agent": m.agent, "created_at": m.created_at.isoformat()}


def _names(db: Session, trip: Trip) -> dict[str, str]:
    return {mem.user_id: mem.user.name for mem in trip.members}


def wants_agent(body: ChatIn) -> bool:
    return body.trigger == "QUICK_ACTION" or bool(MENTION.match(body.text))


async def ask_agent(db: Session, trip: Trip, msg: ChatMessage) -> dict:
    """Send one request to the agent service. Returns the `agent` field saved on the reply message."""
    recent = db.scalars(select(ChatMessage).where(ChatMessage.trip_id == trip.id, ChatMessage.id < msg.id)
                        .order_by(ChatMessage.id.desc()).limit(CONTEXT)).all()
    plan = db.scalar(select(Itinerary).where(Itinerary.trip_id == trip.id).order_by(Itinerary.letter))
    payload = {
        "trip_id": trip.id,
        "itinerary_id": plan.id if plan else None,
        "message": {"id": msg.id, "user_id": msg.author_id, "text": msg.text, "sent_at": msg.created_at.isoformat()},
        "recent_messages": [{"id": r.id, "user_id": r.author_id, "kind": r.kind, "text": r.text,
                             "sent_at": r.created_at.isoformat()} for r in reversed(recent)],
    }
    try:
        out = await services.agent_turn(payload)
    except HTTPException:
        return {"state": "FAILED", "reply": f"{AGENT_NAME} can't answer right now. Please try again in a moment.",
                "tool_calls": [], "proposal": None}
    return {"state": "NEEDS_CLARIFICATION" if out.get("needs_clarification") else "SUCCEEDED",
            "reply": out.get("reply") or "", "tool_calls": out.get("tool_calls") or [],
            "proposal": out.get("proposal")}


@router.get("/trips/{trip_id}/chat")
def list_chat(after: int = 0, trip: Trip = Depends(member_trip), db: Session = Depends(get_db)):
    q = select(ChatMessage).where(ChatMessage.trip_id == trip.id, ChatMessage.id > after)
    rows = db.scalars(q.order_by(ChatMessage.id.desc()).limit(PAGE)).all()[::-1]  # newest PAGE, oldest first
    names = _names(db, trip)
    return {"agent_name": AGENT_NAME, "messages": [message_dict(m, names) for m in rows]}


@router.post("/trips/{trip_id}/chat", status_code=201)
async def post_chat(body: ChatIn, trip: Trip = Depends(member_trip), user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    names = _names(db, trip)
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
    return {"messages": [message_dict(m, names) for m in created]}
