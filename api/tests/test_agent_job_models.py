"""Persistence and constraint tests for agent requests and jobs."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import AgentJob, AgentRequest, TripMember

from .test_agent_read_context import state


@pytest.fixture
def job_state(state):
    db, _, _, _, _ = state

    # SQLite needs this explicitly enabled to enforce foreign keys.
    db.connection().exec_driver_sql("PRAGMA foreign_keys = ON")
    db.commit()

    return state


def request_row(trip, actor, key="request-001"):
    return AgentRequest(
        trip_id=trip.id,
        actor_id=actor.id,
        idempotency_key=key,
        input_hash="a" * 64,
        payload={
            "trigger": "QUICK_ACTION",
            "message": "What are our trip dates?",
            "run_id": None,
            "poi_ids": [],
        },
    )


def test_request_and_pending_job_are_persisted(job_state):
    db, owner, _, trip, _ = job_state

    request = request_row(trip, owner)
    db.add(request)
    db.flush()

    job = AgentJob(request_id=request.id)
    db.add(job)
    db.commit()

    request_id = request.id
    job_id = job.id
    db.expire_all()

    saved_request = db.get(AgentRequest, request_id)
    saved_job = db.get(AgentJob, job_id)

    assert saved_request.schema_version == "agent.v1"
    assert saved_request.payload["message"] == "What are our trip dates?"
    assert saved_request.resolved_run_id is None

    assert saved_job.request_id == request_id
    assert saved_job.state == "PENDING"
    assert saved_job.result is None
    assert saved_job.error_code is None
    assert saved_job.started_at is None
    assert saved_job.finished_at is None


def test_duplicate_request_key_is_rejected(job_state):
    db, owner, _, trip, _ = job_state

    db.add(request_row(trip, owner))
    db.commit()

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(request_row(trip, owner))
            db.flush()


def test_key_is_scoped_by_trip_and_actor(job_state):
    db, owner, outsider, trip, other_trip = job_state

    db.add(
        TripMember(
            trip_id=trip.id,
            user_id=outsider.id,
            role="member",
        )
    )
    db.commit()

    db.add_all([
        request_row(trip, owner),
        request_row(trip, outsider),
        request_row(other_trip, outsider),
    ])
    db.commit()

    count = db.scalar(
        select(func.count()).select_from(AgentRequest)
    )
    assert count == 3


def test_request_cannot_have_two_jobs(job_state):
    db, owner, _, trip, _ = job_state

    request = request_row(trip, owner)
    db.add(request)
    db.flush()

    db.add(AgentJob(request_id=request.id))
    db.commit()

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(AgentJob(request_id=request.id))
            db.flush()


@pytest.mark.parametrize("invalid_state", ["DONE", "COMMITTED"])
def test_invalid_job_state_is_rejected(job_state, invalid_state):
    db, owner, _, trip, _ = job_state

    request = request_row(trip, owner)
    db.add(request)
    db.commit()

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(
                AgentJob(
                    request_id=request.id,
                    state=invalid_state,
                )
            )
            db.flush()


def test_job_requires_an_existing_request(job_state):
    db, _, _, _, _ = job_state

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(AgentJob(request_id="missing-request"))
            db.flush()


def test_deleting_request_also_removes_its_job(job_state):
    db, owner, _, trip, _ = job_state

    request = request_row(trip, owner)
    db.add(request)
    db.flush()

    db.add(AgentJob(request_id=request.id))
    db.commit()

    db.delete(request)
    db.commit()

    count = db.scalar(
        select(func.count()).select_from(AgentJob)
    )
    assert count == 0
    
