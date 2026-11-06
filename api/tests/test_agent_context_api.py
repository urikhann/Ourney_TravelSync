"""HTTP tests for the authenticated agent inspection endpoint."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import event

from app import services, config
from app.routes import agent as agent_routes
from app.db import get_db
from app.routes.agent import router
from app.security import create_access_token

from .test_agent_read_context import make_run, state
from .test_agent_poi_information import poi_state


def auth_headers(user):
    token = create_access_token(user.id, user.token_version)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def http_state(poi_state, monkeypatch):
    db, owner, outsider, trip, other_trip = poi_state

    # Exercise the real current_user dependency with verified test accounts.
    owner.email_verified = True
    outsider.email_verified = True
    db.commit()

    monkeypatch.setattr(config, "FEATURE_AGENT", True)

    test_app = FastAPI()
    test_app.include_router(router)

    def request_db():
        with Session(db.bind, autoflush=False) as session:
            yield session

    test_app.dependency_overrides[get_db] = request_db

    with TestClient(test_app) as client:
        yield client, db, owner, outsider, trip, other_trip


def context_url(trip):
    return f"/trips/{trip.id}/agent/context"

def test_disabled_feature_returns_404(http_state, monkeypatch):
    client, _, owner, _, trip, _ = http_state
    monkeypatch.setattr(config, "FEATURE_AGENT", False)

    response = client.get(
        context_url(trip),
        headers=auth_headers(owner),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "agent_disabled"


def test_login_is_required(http_state):
    client, _, _, _, trip, _ = http_state

    response = client.get(context_url(trip))

    assert response.status_code == 401


@pytest.mark.parametrize("case", ["invalid", "revoked", "unverified"])
def test_invalid_authentication_is_rejected(http_state, case):
    client, db, owner, _, trip, _ = http_state
    headers = auth_headers(owner)

    if case == "invalid":
        headers = {"Authorization": "Bearer invalid-token"}
    elif case == "revoked":
        owner.token_version += 1
        db.commit()
    else:
        owner.email_verified = False
        db.commit()

    response = client.get(context_url(trip), headers=headers)

    assert response.status_code == 401


def test_nonmember_is_rejected(http_state):
    client, _, _, outsider, trip, _ = http_state

    response = client.get(
        context_url(trip),
        headers=auth_headers(outsider),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "trip_not_found"


def test_foreign_run_is_rejected(http_state):
    client, db, owner, _, trip, other_trip = http_state
    foreign_run = make_run(db, other_trip.id)

    response = client.get(
        context_url(trip),
        params={"run_id": foreign_run.id},
        headers=auth_headers(owner),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "recommendation_run_not_found"
    
    
def test_trip_context_without_requested_pois(http_state):
    client, _, owner, _, trip, _ = http_state

    response = client.get(
        context_url(trip),
        headers=auth_headers(owner),
    )

    assert response.status_code == 200
    body = response.json()

    assert body["schema_version"] == "agent.v1"
    assert body["read_only"] is True
    assert body["trip_context"]["trip_id"] == trip.id
    assert body["trip_context"]["recommendation"] is None
    assert body["poi_information"] is None
    assert body["recommendation_evidence"] is None


def test_saved_run_produces_consistent_evidence_bundle(http_state):
    client, db, owner, _, trip, _ = http_state
    run = make_run(db, trip.id)

    run.response = {
        "score_scale": "0-1",
        "individual_method": "tfidf",
        "scorer_version": "test-scorer",
        "group": [
            {
                "poi_id": "kakao:1",
                "group_rank": 1,
                "mean_score": 0.4321,
                "selection_gain": 0.123456,
                "group_utility": 1000,
                "member_scores": {"PRIVATE_MEMBER": 0.9},
            }
        ],
        "removed": [],
    }
    db.commit()

    response = client.get(
        context_url(trip),
        params=[
            ("run_id", str(run.id)),
            ("poi_ids", "kakao:1"),
        ],
        headers=auth_headers(owner),
    )

    assert response.status_code == 200
    body = response.json()

    context = body["trip_context"]
    pois = body["poi_information"]
    evidence = body["recommendation_evidence"]

    assert context["recommendation"]["run_id"] == run.id
    assert pois["run_id"] == run.id
    assert evidence["run"]["run_id"] == run.id

    assert pois["dataset_version"] == "test-dataset"
    assert pois["places"][0]["name"] == "Archived Cafe"
    assert pois["places"][0]["indoor"]["status"] == "UNVERIFIED"

    assert evidence["score_scale"] == "0-1"
    assert evidence["items"][0]["mean_score"] == 0.4321

    for private_value in (
        "PRIVATE_MEMBER",
        "PRIVATE_REQUEST",
        "PRIVATE_METADATA",
        owner.email,
        trip.invite_code,
    ):
        assert private_value not in response.text


def test_pois_without_a_run_use_active_dataset(http_state):
    client, _, owner, _, trip, _ = http_state

    response = client.get(
        context_url(trip),
        params=[("poi_ids", "kakao:1")],
        headers=auth_headers(owner),
    )

    assert response.status_code == 200
    body = response.json()
    pois = body["poi_information"]

    assert body["trip_context"]["recommendation"] is None
    assert pois["run_id"] is None
    assert pois["dataset_basis"] == "ACTIVE_DATASET"
    assert pois["places"][0]["name"] == "Current Cafe"
    assert body["recommendation_evidence"] is None
    
    
@pytest.mark.parametrize(
    "params",
    [
        [("actor_id", "forged-user")],
        [("run_id", "0")],
        [("run_id", "true")],
        [("run_id", "not-a-number")],
        [("poi_ids", " ")],
        [("poi_ids", "same"), ("poi_ids", "same")],
        [("poi_ids", str(i)) for i in range(21)],
    ],
)
def test_invalid_query_parameters_are_rejected(http_state, params):
    client, _, owner, _, trip, _ = http_state

    response = client.get(
        context_url(trip),
        params=params,
        headers=auth_headers(owner),
    )

    assert response.status_code == 422
    
    
def test_changed_context_returns_409(http_state, monkeypatch):
    client, _, owner, _, trip, _ = http_state
    original_lookup = agent_routes.get_poi_information

    def changed_lookup(*args, **kwargs):
        result = original_lookup(*args, **kwargs)

        # Simulate a lookup resolving a different Run.
        return result.model_copy(update={"run_id": 12345})

    monkeypatch.setattr(
        agent_routes,
        "get_poi_information",
        changed_lookup,
    )

    response = client.get(
        context_url(trip),
        params=[("poi_ids", "kakao:1")],
        headers=auth_headers(owner),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "agent_context_changed"
    

def test_malformed_evidence_returns_safe_http_error(http_state):
    client, db, owner, _, trip, _ = http_state
    run = make_run(db, trip.id)

    run.response = {"group": "PRIVATE_MALFORMED_CONTENT"}
    db.commit()

    response = client.get(
        context_url(trip),
        params=[
            ("run_id", str(run.id)),
            ("poi_ids", "kakao:1"),
        ],
        headers=auth_headers(owner),
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "invalid_recommendation_evidence"
    assert "PRIVATE_MALFORMED_CONTENT" not in response.text
    
def test_http_request_does_not_write_or_call_services(
    http_state, monkeypatch
):
    client, db, owner, _, trip, _ = http_state
    run = make_run(db, trip.id)

    def forbidden_service_call(*args, **kwargs):
        pytest.fail("Inspection must not call computation services.")

    for name in (
        "recommend",
        "build_plans",
        "check_availability",
        "agent_turn",
    ):
        monkeypatch.setattr(services, name, forbidden_service_call)

    statements = []

    def allow_only_selects(
        connection, cursor, statement, parameters, context, executemany
    ):
        statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT"), statement

    event.listen(db.bind, "before_cursor_execute", allow_only_selects)

    try:
        response = client.get(
            context_url(trip),
            params=[
                ("run_id", str(run.id)),
                ("poi_ids", "kakao:1"),
            ],
            headers=auth_headers(owner),
        )

        assert response.status_code == 200
        assert statements
    finally:
        event.remove(db.bind, "before_cursor_execute", allow_only_selects)
        
def test_full_application_registration_and_disabled_core_access(
    http_state, monkeypatch
):
    client, _, owner, _, trip, _ = http_state

    # Import the actual application rather than our small test app.
    from app.main import app as application

    # Reuse the isolated database dependency from the test client.
    request_db = client.app.dependency_overrides[get_db]
    monkeypatch.setitem(
        application.dependency_overrides,
        get_db,
        request_db,
    )

    with TestClient(application) as integrated_client:
        response = integrated_client.get(
            context_url(trip),
            headers=auth_headers(owner),
        )
        assert response.status_code == 200
        assert response.json()["trip_context"]["trip_id"] == trip.id

        monkeypatch.setattr(config, "FEATURE_AGENT", False)

        disabled = integrated_client.get(
            context_url(trip),
            headers=auth_headers(owner),
        )
        assert disabled.status_code == 404

        existing_trip = integrated_client.get(
            f"/trips/{trip.id}",
            headers=auth_headers(owner),
        )
        assert existing_trip.status_code == 200
        assert existing_trip.json()["id"] == trip.id
        
