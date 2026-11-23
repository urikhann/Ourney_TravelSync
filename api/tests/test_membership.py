"""Issue #18: leave a trip, delete a trip or an account, and invite links that expire or get revoked."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, text

from app.db import SessionLocal, engine
from app.models import (ChatMessage, MemberAvailability, OptimizationRun, Preference, ProposalVote, Trip,
                        TripInvite, User)
from .test_flow import FEATURES, c, signup
from .test_proposal_approval import ask, planned_trip, swap_for, vote

DAY = "2026-11-06"


def new_trip(h, name="Issue 18"):
    r = c.post("/trips", headers=h, json=dict(name=name, start_date=DAY, end_date=DAY))
    assert r.status_code == 201, r.text
    return r.json()


def people(n):
    return [signup("Mb" + uuid4().hex[:10]) for _ in range(n)]


def no_broken_links():
    """Every foreign key still points at a row (SQLite does not enforce them, so check them all)."""
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def count(model, **where):
    with SessionLocal() as db:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.scalar(q)


def test_invite_link_expires_and_the_owner_can_renew_it():
    owner, friend = people(2)
    trip = new_trip(owner)
    assert trip["invite"]["status"] == "active" and trip["is_owner"] is True
    with SessionLocal() as db:  # 8 days later
        db.get(TripInvite, trip["id"]).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert c.get(f"/invites/{trip['invite_code']}", headers=friend).json()["detail"] == "invite_expired"
    r = c.post(f"/trips/join/{trip['invite_code']}", headers=friend)
    assert r.status_code == 410 and r.json()["detail"] == "invite_expired"
    assert c.get(f"/trips/{trip['id']}", headers=owner).json()["invite"]["status"] == "expired"

    assert c.post(f"/trips/{trip['id']}/invite", headers=friend, json={"days": 3}).status_code == 404  # not a member
    renewed = c.post(f"/trips/{trip['id']}/invite", headers=owner, json={"days": 3}).json()
    assert renewed["invite"]["status"] == "active" and renewed["invite_code"] != trip["invite_code"]
    left = datetime.fromisoformat(renewed["invite"]["expires_at"]) - datetime.now(timezone.utc)
    assert timedelta(days=2, hours=23) < left <= timedelta(days=3)
    assert c.post(f"/trips/join/{trip['invite_code']}", headers=friend).status_code == 404  # the old link is dead
    assert c.post(f"/trips/join/{renewed['invite_code']}", headers=friend).status_code == 200


def test_owner_can_turn_the_link_off():
    owner, friend, late = people(3)
    trip = new_trip(owner)
    assert c.post(f"/trips/join/{trip['invite_code']}", headers=friend).status_code == 200
    assert c.delete(f"/trips/{trip['id']}/invite", headers=friend).status_code == 403  # only the owner
    off = c.delete(f"/trips/{trip['id']}/invite", headers=owner).json()
    assert off["invite"]["status"] == "revoked"
    assert c.post(f"/trips/join/{trip['invite_code']}", headers=late).status_code == 404
    r = c.post(f"/trips/join/{off['invite_code']}", headers=late)
    assert r.status_code == 410 and r.json()["detail"] == "invite_revoked"
    assert c.get(f"/invites/{off['invite_code']}", headers=friend).status_code == 200  # members can still open it
    on = c.post(f"/trips/{trip['id']}/invite", headers=owner).json()
    assert on["invite"]["status"] == "active"
    assert c.post(f"/trips/join/{on['invite_code']}", headers=late).status_code == 200


def test_leaving_cleans_up_and_hands_over_ownership():
    owner, friend = people(2)
    trip = new_trip(owner)
    tid = trip["id"]
    assert c.post(f"/trips/join/{trip['invite_code']}", headers=friend).status_code == 200
    for h in (owner, friend):
        c.put(f"/trips/{tid}/availability", headers=h, json={"dates": [DAY]})
        c.put(f"/trips/{tid}/preferences/me", headers=h, json={"ratings": dict.fromkeys(FEATURES, 3)})
    c.post(f"/trips/{tid}/chat", headers=owner, json={"text": "see you there"})
    owner_id = next(m["user_id"] for m in trip["members"] if m["is_me"])

    assert c.post(f"/trips/{tid}/leave", headers=owner).json() == {"left": True, "trip_deleted": False}
    assert c.get(f"/trips/{tid}", headers=owner).status_code == 404
    after = c.get(f"/trips/{tid}", headers=friend).json()
    assert after["is_owner"] is True and [m["role"] for m in after["members"]] == ["owner"]
    assert count(Preference, trip_id=tid, user_id=owner_id) == 0
    assert count(MemberAvailability, trip_id=tid, user_id=owner_id) == 0
    msgs = c.get(f"/trips/{tid}/chat", headers=friend).json()["messages"]
    assert msgs[-1]["author"]["name"] == "Former member"  # their messages stay

    assert c.post(f"/trips/{tid}/leave", headers=friend).json() == {"left": True, "trip_deleted": True}
    assert count(Trip, id=tid) == 0
    no_broken_links()


def test_owner_removes_a_member():
    owner, friend = people(2)
    trip = new_trip(owner)
    c.post(f"/trips/join/{trip['invite_code']}", headers=friend)
    detail = c.get(f"/trips/{trip['id']}", headers=owner).json()
    friend_id = next(m["user_id"] for m in detail["members"] if not m["is_me"])
    owner_id = next(m["user_id"] for m in detail["members"] if m["is_me"])
    assert c.delete(f"/trips/{trip['id']}/members/{owner_id}", headers=friend).status_code == 403
    assert c.delete(f"/trips/{trip['id']}/members/{owner_id}", headers=owner).status_code == 409  # use leave
    r = c.delete(f"/trips/{trip['id']}/members/{friend_id}", headers=owner)
    assert r.status_code == 200 and len(r.json()["members"]) == 1
    assert c.get(f"/trips/{trip['id']}", headers=friend).status_code == 404


def test_deleting_a_planned_trip_removes_everything(planning_adapter):
    (a, b), tid, plan = planned_trip(2)
    _, _, proposal = swap_for(tid, plan)
    msg = ask(b, tid, proposal)
    assert vote(a, tid, msg["id"]).status_code == 200  # a pending vote on a suggestion
    assert c.delete(f"/trips/{tid}", headers=b).status_code == 403  # members can't delete it
    assert c.delete(f"/trips/{tid}", headers=a).status_code == 204
    assert c.get(f"/trips/{tid}", headers=a).status_code == 404
    for model in (ChatMessage, OptimizationRun, Preference, MemberAvailability):
        assert count(model, trip_id=tid) == 0
    assert count(ProposalVote, message_id=msg["id"]) == 0
    no_broken_links()


def test_deleting_an_account(planning_adapter):
    (a, b), tid, plan = planned_trip(2)
    solo = new_trip(a, "Just me")
    a_id = c.get("/me", headers=a).json()["id"]
    c.post(f"/trips/{tid}/chat", headers=a, json={"text": "I made the plan"})

    assert c.post("/me/delete", headers=a, json={"password": "wrong-password"}).status_code == 403
    assert c.post("/me/delete", headers=a, json={"password": "password123"}).status_code == 204
    assert c.get("/me", headers=a).status_code == 401  # the old login token no longer works
    assert count(User, id=a_id) == 0
    assert count(Trip, id=solo["id"]) == 0  # nobody else was in it

    shared = c.get(f"/trips/{tid}", headers=b).json()  # the other member keeps the trip and becomes the owner
    assert shared["is_owner"] is True and len(shared["members"]) == 1
    assert c.get(f"/trips/{tid}/itineraries", headers=b).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(OptimizationRun).where(OptimizationRun.requested_by == a_id)) == 0
    names = [m["author"]["name"] for m in c.get(f"/trips/{tid}/chat", headers=b).json()["messages"] if m["author"]]
    assert "Former member" in names
    no_broken_links()
