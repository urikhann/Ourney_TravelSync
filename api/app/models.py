"""Database tables.

Bat owns everything except the place tables at the bottom (owned by the data pipeline).
While the schema is changing fast, tables are created on startup. Switch to Alembic migrations
once the team starts sharing a database with real data.
"""
import secrets
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String,
                        UniqueConstraint, ForeignKeyConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def new_invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
    return "".join(secrets.choice(alphabet) for _ in range(8))


# ---------------------------------------------------------------- accounts
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UserTaste(Base):
    """Onboarding survey answers, one row per user (see app/taste_catalog.py). status: "done" or "skipped"."""
    __tablename__ = "user_tastes"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)   # {"food": ["seafood"], "places": [...], "activities": [...], "avoid": [...]}
    status: Mapped[str] = mapped_column(String(10), default="done")
    version: Mapped[str] = mapped_column(String(20), default="taste-1.0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class EmailCode(Base):
    __tablename__ = "email_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------- trips
class Trip(Base):
    __tablename__ = "trips"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80))
    city: Mapped[str] = mapped_column(String(40), default="busan")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    invite_code: Mapped[str] = mapped_column(String(8), unique=True, index=True, default=new_invite_code)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    members: Mapped[list["TripMember"]] = relationship(back_populates="trip", cascade="all, delete-orphan")


class TripMember(Base):
    __tablename__ = "trip_members"
    __table_args__ = (UniqueConstraint("trip_id", "user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")  # owner | member
    # Personal "never take me there" rules (SDD 6.6 member_exclusions): {"tags": [...], "poi_ids": [...]}
    exclusions: Mapped[dict] = mapped_column(JSON, default=lambda: {"tags": [], "poi_ids": []})
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    trip: Mapped[Trip] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class MemberAvailability(Base):
    """One row per member per free date."""
    __tablename__ = "availability"
    __table_args__ = (UniqueConstraint("trip_id", "user_id", "day"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)


class Preference(Base):
    """One row per saved version (SDD 6.3): saving again INSERTs version+1, it never overwrites.
    ratings = the 10 raw 1–5 scores; the recommender derives the weights w = r / sum(r) itself."""
    __tablename__ = "preferences"
    __table_args__ = (UniqueConstraint("trip_id", "user_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    ratings: Mapped[dict] = mapped_column(JSON)            # {"food": 5, "cafe": 4, ...} — exactly the 10 features
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(20), default="gfar")      # gfar | average | awm (comparison)
    misery_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)  # only for awm, 0–1 scale
    dataset_version: Mapped[str] = mapped_column(String(40), default="")
    feature_schema_version: Mapped[str] = mapped_column(String(40), default="")
    member_snapshot: Mapped[list] = mapped_column(JSON, default=list)     # [{"user_id", "preference_version"}]
    request: Mapped[dict] = mapped_column(JSON)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Itinerary(Base):
    """One candidate plan (A, B or C) built by the optimizer from a recommendation run."""
    __tablename__ = "itineraries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("recommendation_runs.id", ondelete="CASCADE"))
    letter: Mapped[str] = mapped_column(String(1))           # "A", "B", "C"
    variant: Mapped[str] = mapped_column(String(20))         # "best", "culture", "food"
    days: Mapped[list] = mapped_column(JSON)                 # [{date, items: [{poi_id, start, end, ...}]}]
    stats: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Vote(Base):
    """One vote per member per trip (voting again replaces it)."""
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("trip_id", "user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    itinerary_id: Mapped[int] = mapped_column(ForeignKey("itineraries.id", ondelete="CASCADE"))
    voted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OptimizationRun(Base):
    """Private optimizer audit. Additive table; existing itinerary schema is unchanged.

    Includes failed attempts so unknown-data/timeout outcomes are reproducible
    without publishing a fake or empty itinerary. Never serialize these private
    snapshots directly into group-facing responses.
    """
    __tablename__ = "itinerary_optimization_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    recommendation_run_id: Mapped[int] = mapped_column(ForeignKey("recommendation_runs.id"))
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    itinerary_id: Mapped[int | None] = mapped_column(ForeignKey("itineraries.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    planning_data: Mapped[dict] = mapped_column(JSON, default=dict)
    optimizer_request: Mapped[dict] = mapped_column(JSON, default=dict)
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------- places (data pipeline owns these)
class DatasetVersion(Base):
    """An imported, validated set of places (SDD 6.11). Exactly one is ACTIVE; a run pins the version it used."""
    __tablename__ = "dataset_versions"
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT")   # DRAFT | ACTIVE | ARCHIVED
    feature_schema_version: Mapped[str] = mapped_column(String(40))
    mapping_version: Mapped[str] = mapped_column(String(40))
    poi_count: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Poi(Base):
    """One place inside one dataset version. Key = (id, dataset_version), so old runs keep their places.
    Unknown facts stay NULL (never 0 won / 0 min / always open) and field_status says KNOWN / ESTIMATED / UNKNOWN."""
    __tablename__ = "pois"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)                 # e.g. "kakao:8732558", "tour:129156"
    dataset_version: Mapped[str] = mapped_column(ForeignKey("dataset_versions.version"), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_ko: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str] = mapped_column(String(40), index=True, default="busan")
    area: Mapped[str] = mapped_column(String(60), default="")                    # 구/군, e.g. "해운대구"
    kind: Mapped[str] = mapped_column(String(20), default="activity")            # activity | food (for meal slots)
    category: Mapped[str] = mapped_column(String(200), default="")               # source category, e.g. "음식점 > 카페"
    address: Mapped[str] = mapped_column(String(300), default="")
    road_address: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    place_url: Mapped[str] = mapped_column(String(300), default="")
    image_url: Mapped[str] = mapped_column(String(500), default="")
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    indoor: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    avg_cost_krw: Mapped[int | None] = mapped_column(Integer, nullable=True)     # per person; NULL = unknown
    duration_min: Mapped[int] = mapped_column(Integer, default=60)               # see field_status: usually ESTIMATED
    tags: Mapped[list] = mapped_column(JSON, default=list)                       # ["hiking"], ["raw_fish"], ["alcohol"]
    features: Mapped[dict] = mapped_column(JSON, default=dict)                   # the 10 features, each 0 or 1
    _legacy_enriched_metadata: Mapped[dict] = mapped_column('enriched_metadata', JSON, default=dict)
    metadata_record: Mapped['PoiMetadata | None'] = relationship(lazy='joined', cascade='save-update, merge', uselist=False)
    feature_rules: Mapped[str] = mapped_column(String(500), default="")          # why each feature was given
    field_status: Mapped[dict] = mapped_column(JSON, default=dict)               # {"hours": "UNKNOWN", "cost": "UNKNOWN", ...}
    walking_level: Mapped[float] = mapped_column(Float, default=0.0)
    closed_on_public_holidays: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(40), default="sample")            # KAKAO | TOUR_API | TOUR_API+KAKAO | sample
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    @property
    def enriched_metadata(self):
        return self.metadata_record.metadata_json if self.metadata_record else {}

    @enriched_metadata.setter
    def enriched_metadata(self, value):
        self.metadata_record = PoiMetadata(metadata_json=value) if value else None


class PoiMetadata(Base):
    __tablename__ = 'poi_metadata'
    __table_args__ = (ForeignKeyConstraint(['place_id', 'dataset_version'], ['pois.id', 'pois.dataset_version']),)
    place_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_version: Mapped[str] = mapped_column(String(40), primary_key=True)
    metadata_json: Mapped[dict] = mapped_column('metadata', JSON, default=dict)


class PoiHours(Base):
    __tablename__ = "poi_hours"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poi_id: Mapped[str] = mapped_column(String(64), index=True)
    dow: Mapped[int] = mapped_column(Integer)          # 0 = Sunday … 6 = Saturday
    open: Mapped[str] = mapped_column(String(5))       # "09:00"
    close: Mapped[str] = mapped_column(String(5))      # "17:30" ("24:00" = midnight)


class PoiException(Base):
    __tablename__ = "poi_exceptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poi_id: Mapped[str] = mapped_column(String(64), index=True)
    day: Mapped[date] = mapped_column(Date)
    closed: Mapped[bool] = mapped_column(Boolean, default=True)
    open: Mapped[str | None] = mapped_column(String(5), nullable=True)
    close: Mapped[str | None] = mapped_column(String(5), nullable=True)
    reason: Mapped[str] = mapped_column(String(120), default="")


class PublicHoliday(Base):
    __tablename__ = "public_holidays"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(60))


# ---------------------------------------------------------------- trip chat

class ChatMessage(Base):
    """One message in a trip's group chat. kind: "user" (a member) or "agent" (TravelSync's reply).
    Agent replies keep the agent's answer in `agent` (state, tool_calls, proposal), see contracts/agent.*.json."""
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), default="user")
    text: Mapped[str] = mapped_column(String(2000))
    agent: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reply_to: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the member message an agent reply answers
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
