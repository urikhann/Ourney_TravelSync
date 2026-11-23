"""Leaving trips, deleting trips and accounts, and invite links that expire or get revoked (issue #18).

Rules
  leave        a member leaves; their dates, ratings and votes for that trip are removed.
               If the owner leaves, the member who joined earliest becomes the owner.
               If nobody is left, the trip is deleted.
  remove       the owner removes another member (same clean-up as leaving).
  delete trip  owner only; everything that belongs to the trip is deleted.
  delete me    needs the password; the user leaves every trip (same rules), then the account and
               everything personal is deleted. Their old chat messages stay, shown as "Former member".
  invite       a link works for INVITE_TTL_DAYS days. The owner can make a new link (new code + new expiry)
               or turn the link off (the code changes, so every old link stops working).

Rows are deleted explicitly, child tables first. That keeps the database consistent on PostgreSQL
(some foreign keys have no ON DELETE rule) and on SQLite (which does not enforce foreign keys by default).
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from . import config
from .models import (ChatMessage, EmailCode, Itinerary, MemberAvailability, OptimizationRun, Preference,
                     ProposalVote, RecommendationRun, Trip, TripInvite, TripMember, User, UserPreference, UserTaste, Vote,
                     new_invite_code)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops the timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- invites
def invite(db: Session, trip: Trip) -> TripInvite:
    """The trip's invite settings. Trips made before issue #18 get a fresh 7-day link the first time."""
    row = db.get(TripInvite, trip.id)
    if row is None:
        row = TripInvite(trip_id=trip.id, expires_at=now() + timedelta(days=config.INVITE_TTL_DAYS))
        db.add(row)
        db.flush()
    return row


def invite_status(db: Session, trip: Trip) -> str:
    row = invite(db, trip)
    if row.revoked:
        return "revoked"
    return "expired" if _utc(row.expires_at) <= now() else "active"


def check_invite(db: Session, trip: Trip) -> None:
    """Raise if this trip's link can't be used to join right now."""
    status = invite_status(db, trip)
    if status != "active":
        db.commit()
        raise HTTPException(410, f"invite_{status}")


def _new_code(db: Session) -> str:
    while True:
        code = new_invite_code()
        if not db.scalar(select(Trip.id).where(Trip.invite_code == code)):
            return code


def renew_invite(db: Session, trip: Trip, days: int) -> None:
    """A new link: new code (old links stop working) that works for `days` days."""
    row = invite(db, trip)
    trip.invite_code = _new_code(db)
    row.expires_at, row.revoked = now() + timedelta(days=days), False
    db.commit()


def revoke_invite(db: Session, trip: Trip) -> None:
    """Turn the link off. The code changes too, so nobody can join with an old link."""
    row = invite(db, trip)
    trip.invite_code = _new_code(db)
    row.revoked = True
    db.commit()


# ---------------------------------------------------------------- members and trips
def require_owner(trip: Trip, user: User) -> None:
    if trip.created_by != user.id:
        raise HTTPException(403, "owner_only")


def remove_member(db: Session, trip: Trip, user_id: str) -> bool:
    """Take one member out of a trip. Returns True if the trip was deleted because nobody is left."""
    member = next((m for m in trip.members if m.user_id == user_id), None)
    if member is None:
        raise HTTPException(404, "member_not_found")
    others = sorted((m for m in trip.members if m.user_id != user_id), key=lambda m: (m.joined_at, m.id))
    if not others:
        delete_trip(db, trip)
        return True

    message_ids = select(ChatMessage.id).where(ChatMessage.trip_id == trip.id)
    db.execute(delete(ProposalVote).where(ProposalVote.user_id == user_id, ProposalVote.message_id.in_(message_ids)))
    db.execute(delete(Vote).where(Vote.trip_id == trip.id, Vote.user_id == user_id))
    db.execute(delete(MemberAvailability).where(MemberAvailability.trip_id == trip.id,
                                                MemberAvailability.user_id == user_id))
    db.execute(delete(Preference).where(Preference.trip_id == trip.id, Preference.user_id == user_id))
    if trip.created_by == user_id:  # the owner leaves: the earliest member takes over
        others[0].role = "owner"
        trip.created_by = others[0].user_id
    db.execute(update(OptimizationRun).where(OptimizationRun.trip_id == trip.id,
                                             OptimizationRun.requested_by == user_id)
               .values(requested_by=trip.created_by))
    trip.members.remove(member)
    db.delete(member)
    db.commit()
    return False


def delete_trip(db: Session, trip: Trip) -> None:
    tid = trip.id
    message_ids = select(ChatMessage.id).where(ChatMessage.trip_id == tid)
    db.execute(delete(ProposalVote).where(ProposalVote.message_id.in_(message_ids)))
    db.execute(delete(ChatMessage).where(ChatMessage.trip_id == tid))
    db.execute(delete(Vote).where(Vote.trip_id == tid))
    db.execute(delete(OptimizationRun).where(OptimizationRun.trip_id == tid))
    db.execute(delete(Itinerary).where(Itinerary.trip_id == tid))
    db.execute(delete(RecommendationRun).where(RecommendationRun.trip_id == tid))
    db.execute(delete(Preference).where(Preference.trip_id == tid))
    db.execute(delete(MemberAvailability).where(MemberAvailability.trip_id == tid))
    db.execute(delete(TripInvite).where(TripInvite.trip_id == tid))
    db.execute(delete(TripMember).where(TripMember.trip_id == tid))
    db.expunge(trip)
    db.execute(delete(Trip).where(Trip.id == tid))
    db.commit()


def delete_account(db: Session, user: User) -> None:
    uid = user.id
    trip_ids = db.scalars(select(TripMember.trip_id).where(TripMember.user_id == uid)).all()
    for tid in trip_ids:
        trip = db.get(Trip, tid)
        if trip:
            remove_member(db, trip, uid)
    # Anything left over (e.g. from trips the user was removed from earlier) goes too.
    db.execute(update(ChatMessage).where(ChatMessage.author_id == uid).values(author_id=None))
    owner = select(Trip.created_by).where(Trip.id == OptimizationRun.trip_id).scalar_subquery()
    db.execute(update(OptimizationRun).where(OptimizationRun.requested_by == uid).values(requested_by=owner))
    db.execute(delete(ProposalVote).where(ProposalVote.user_id == uid))
    db.execute(delete(Vote).where(Vote.user_id == uid))
    db.execute(delete(MemberAvailability).where(MemberAvailability.user_id == uid))
    db.execute(delete(Preference).where(Preference.user_id == uid))
    db.execute(delete(TripMember).where(TripMember.user_id == uid))
    db.execute(delete(UserTaste).where(UserTaste.user_id == uid))
    db.execute(delete(UserPreference).where(UserPreference.user_id == uid))
    db.execute(delete(EmailCode).where(EmailCode.user_id == uid))
    db.expunge(user)
    db.execute(delete(User).where(User.id == uid))
    db.commit()
