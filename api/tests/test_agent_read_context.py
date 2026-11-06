"""Behavior tests for the read-only trip-context tool."""

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, delete, event
from sqlalchemy.orm import Session

from app.agent.read_tools import get_trip_context
from app.agent.schemas import TripContextInput
from app.db import Base
from app.models import RecommendationRun, Trip, TripMember, User
from sqlalchemy.pool import StaticPool


@pytest.fixture
def state():
    """Create a fresh in-memory database for each test."""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as db:
        owner = User(
            id="owner",
            email="owner@example.com",
            name="Owner",
            password_hash="unused-test-value",
        )
        outsider = User(
            id="outsider",
            email="outsider@example.com",
            name="Outsider",
            password_hash="unused-test-value",
        )
        db.add_all([owner, outsider])
        db.commit()

        trip = Trip(
            id="trip-a",
            name="Busan weekend",
            city="busan",
            start_date=date(2026, 11, 7),
            end_date=date(2026, 11, 8),
            created_by=owner.id,
        )
        other_trip = Trip(
            id="trip-b",
            name="Another trip",
            city="busan",
            start_date=date(2026, 11, 9),
            end_date=date(2026, 11, 9),
            created_by=outsider.id,
        )
        db.add_all([trip, other_trip])
        db.commit()

        db.add_all([
            TripMember(
                trip_id=trip.id,
                user_id=owner.id,
                role="owner",
                exclusions={"poi_ids": ["PRIVATE_EXCLUSION"]},
            ),
            TripMember(
                trip_id=other_trip.id,
                user_id=outsider.id,
                role="owner",
            ),
        ])
        db.commit()

        yield db, owner, outsider, trip, other_trip

    engine.dispose()


def make_run(db, trip_id):
    """Store a synthetic recommendation result without running a service."""

    run = RecommendationRun(
        trip_id=trip_id,
        model_version="test-model",
        method="gfar",
        dataset_version="test-dataset",
        feature_schema_version="fs-1.0",
        member_snapshot=[],
        request={"private": "PRIVATE_REQUEST"},
        response={"private": "PRIVATE_RESPONSE"},
    )
    db.add(run)
    db.commit()
    return run


def test_member_can_read_trip_without_recommendations(state):
    db, owner, _, trip, _ = state

    result = get_trip_context(
        TripContextInput(trip_id=trip.id),
        user=owner,
        db=db,
    )

    assert result.trip_id == trip.id
    assert result.name == "Busan weekend"
    assert result.start_date == date(2026, 11, 7)
    assert result.end_date == date(2026, 11, 8)
    assert result.member_count == 1
    assert result.recommendation is None
    assert result.read_only is True


def test_latest_run_and_explicit_run_selection(state):
    db, owner, _, trip, _ = state
    first = make_run(db, trip.id)
    second = make_run(db, trip.id)

    latest = get_trip_context(
        TripContextInput(trip_id=trip.id),
        user=owner,
        db=db,
    )
    explicit = get_trip_context(
        TripContextInput(trip_id=trip.id, run_id=first.id),
        user=owner,
        db=db,
    )

    assert latest.recommendation.run_id == second.id
    assert explicit.recommendation.run_id == first.id
    assert explicit.recommendation.dataset_version == "test-dataset"


def test_nonmember_cannot_read_trip(state):
    db, _, outsider, trip, _ = state

    with pytest.raises(HTTPException) as error:
        get_trip_context(
            TripContextInput(trip_id=trip.id),
            user=outsider,
            db=db,
        )

    assert error.value.status_code == 404
    assert error.value.detail == "trip_not_found"


def test_run_from_another_trip_is_rejected(state):
    db, owner, _, trip, other_trip = state
    foreign_run = make_run(db, other_trip.id)

    with pytest.raises(HTTPException) as error:
        get_trip_context(
            TripContextInput(trip_id=trip.id, run_id=foreign_run.id),
            user=owner,
            db=db,
        )

    assert error.value.status_code == 404
    assert error.value.detail == "recommendation_run_not_found"


def test_membership_is_rechecked_on_each_call(state):
    db, owner, _, trip, _ = state
    request = TripContextInput(trip_id=trip.id)

    get_trip_context(request, user=owner, db=db)

    db.execute(
        delete(TripMember).where(
            TripMember.trip_id == trip.id,
            TripMember.user_id == owner.id,
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as error:
        get_trip_context(request, user=owner, db=db)

    assert error.value.status_code == 404


def test_output_excludes_private_data(state):
    db, owner, _, trip, _ = state
    make_run(db, trip.id)

    result = get_trip_context(
        TripContextInput(trip_id=trip.id),
        user=owner,
        db=db,
    )
    output = result.model_dump_json()

    for private_value in (
        "PRIVATE_REQUEST",
        "PRIVATE_RESPONSE",
        "PRIVATE_EXCLUSION",
        owner.email,
        owner.password_hash,
        trip.invite_code,
    ):
        assert private_value not in output


def test_read_does_not_flush_pending_changes_or_write(state):
    db, owner, _, trip, _ = state

    # A normal Session may flush this pending update before a query.
    # Our tool must prevent that.
    owner.name = "Pending change"

    statements = []

    def allow_only_selects(
        connection, cursor, statement, parameters, context, executemany
    ):
        statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT"), statement

    event.listen(db.bind, "before_cursor_execute", allow_only_selects)

    try:
        get_trip_context(
            TripContextInput(trip_id=trip.id),
            user=owner,
            db=db,
        )

        assert statements
        assert owner in db.dirty
    finally:
        event.remove(db.bind, "before_cursor_execute", allow_only_selects)
        db.rollback()
