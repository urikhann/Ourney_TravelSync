"""Accuracy, privacy and failure tests for saved recommendation evidence."""

from copy import deepcopy

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app import services
from app.agent.read_tools import get_recommendation_evidence
from app.agent.schemas import RecommendationEvidenceInput

from .test_agent_read_context import make_run, state


@pytest.fixture
def evidence_state(state):
    db, owner, outsider, trip, other_trip = state
    run = make_run(db, trip.id)

    run.response = {
        "score_scale": "0-1",
        "individual_method": "tfidf",
        "scorer_version": "method-c-char23-v1",
        "private": "PRIVATE_RESPONSE",
        "group": [
            {
                "poi_id": "kakao:1",
                "group_rank": 1,
                "mean_score": 0.4321,
                "selection_gain": 0.123456,
                "group_utility": 1000,
                "member_scores": {"PRIVATE_MEMBER": 0.9876},
                "picked_for": "PRIVATE_MEMBER",
                "reasons": [
                    {
                        "code": "fair_pick",
                        "text": "PRIVATE_REASON",
                        "user_id": "PRIVATE_MEMBER",
                    }
                ],
            }
        ],
        "removed": [
            {
                "poi_id": "kakao:2",
                "stage": "E1",
                "rule": "member_exclusion",
                "detail": "PRIVATE_EXCLUSION",
            },
            {
                "poi_id": "kakao:2",
                "stage": "E1",
                "rule": "PRIVATE_CUSTOM_RULE",
                "detail": "PRIVATE_DETAIL",
            },
        ],
        "individual": {"PRIVATE_MEMBER": []},
        "metrics": {"private": "PRIVATE_METRICS"},
    }
    db.commit()

    return db, owner, outsider, trip, other_trip, run


def read_evidence(data, poi_ids):
    """Call the tool as the fixture's trip owner."""

    db, owner, _, trip, _, run = data

    return get_recommendation_evidence(
        RecommendationEvidenceInput(
            trip_id=trip.id,
            run_id=run.id,
            poi_ids=poi_ids,
        ),
        user=owner,
        db=db,
    )
    
def test_saved_values_are_preserved(evidence_state):
    result = read_evidence(evidence_state, ["kakao:1"])
    item = result.items[0]
    run = evidence_state[-1]

    assert result.run.run_id == run.id
    assert result.run.dataset_version == "test-dataset"
    assert result.score_scale == "0-1"
    assert result.individual_method == "tfidf"
    assert result.scorer_version == "method-c-char23-v1"

    assert item.state == "SELECTED"
    assert item.source_section == "group"
    assert item.group_rank == 1
    assert item.mean_score == 0.4321
    assert item.selection_gain == 0.123456
    assert item.group_utility == 1000

    assert result.scope == "RECOMMENDATION_CANDIDATES"
    assert result.causal_attribution_available is False


def test_states_and_requested_order_are_preserved(evidence_state):
    result = read_evidence(
        evidence_state,
        ["missing:123", "kakao:2", "kakao:1"],
    )

    assert [item.poi_id for item in result.items] == [
        "missing:123",
        "kakao:2",
        "kakao:1",
    ]
    assert [item.state for item in result.items] == [
        "NOT_RECORDED",
        "EXCLUDED",
        "SELECTED",
    ]

    missing, excluded, _ = result.items

    assert missing.source_section is None
    assert missing.mean_score is None
    assert missing.exclusion_codes == []

    assert excluded.source_section == "removed"
    assert excluded.group_rank is None
    assert excluded.exclusion_codes == [
        "MEMBER_EXCLUSION",
        "OTHER_RECORDED_EXCLUSION",
    ]


def test_private_fields_are_not_forwarded(evidence_state):
    result = read_evidence(evidence_state, ["kakao:1", "kakao:2"])
    output = result.model_dump_json()
    _, owner, _, trip, _, _ = evidence_state

    for private_value in (
        "PRIVATE_REQUEST",
        "PRIVATE_RESPONSE",
        "PRIVATE_MEMBER",
        "PRIVATE_REASON",
        "PRIVATE_EXCLUSION",
        "PRIVATE_CUSTOM_RULE",
        "PRIVATE_DETAIL",
        "PRIVATE_METRICS",
        owner.email,
        owner.password_hash,
        trip.invite_code,
    ):
        assert private_value not in output


def test_absent_metadata_is_not_guessed(evidence_state):
    db, _, _, _, _, run = evidence_state
    payload = deepcopy(run.response)

    for field in ("score_scale", "individual_method", "scorer_version"):
        payload.pop(field)

    run.response = payload
    db.commit()

    result = read_evidence(evidence_state, ["kakao:1"])

    assert result.score_scale is None
    assert result.individual_method is None
    assert result.scorer_version is None


def test_missing_numeric_evidence_is_not_replaced_with_zero(evidence_state):
    db, _, _, _, _, run = evidence_state
    payload = deepcopy(run.response)

    payload["group"][0].pop("selection_gain")
    payload["group"][0].pop("group_utility")

    run.response = payload
    db.commit()

    item = read_evidence(evidence_state, ["kakao:1"]).items[0]

    assert item.state == "SELECTED"
    assert item.selection_gain is None
    assert item.group_utility is None
    
@pytest.mark.parametrize(
    "case",
    ["nonmember", "foreign_run", "missing_run"],
)
def test_unauthorized_or_missing_run_is_rejected(evidence_state, case):
    db, owner, outsider, trip, other_trip, run = evidence_state

    user = owner
    run_id = run.id
    expected_detail = "recommendation_run_not_found"

    if case == "nonmember":
        user = outsider
        expected_detail = "trip_not_found"
    elif case == "foreign_run":
        run_id = make_run(db, other_trip.id).id
    else:
        run_id = 999999

    with pytest.raises(HTTPException) as error:
        get_recommendation_evidence(
            RecommendationEvidenceInput(
                trip_id=trip.id,
                run_id=run_id,
                poi_ids=["kakao:1"],
            ),
            user=user,
            db=db,
        )

    assert error.value.status_code == 404
    assert error.value.detail == expected_detail


@pytest.mark.parametrize(
    "corruption",
    [
        "section",
        "row",
        "poi_id",
        "number",
        "duplicate",
        "conflict",
        "scale",
    ],
)
def test_malformed_evidence_returns_sanitized_error(
    evidence_state, corruption
):
    db, _, _, _, _, run = evidence_state
    payload = deepcopy(run.response)

    if corruption == "section":
        payload["group"] = "PRIVATE_BAD_SECTION"
    elif corruption == "row":
        payload["group"] = ["PRIVATE_BAD_ROW"]
    elif corruption == "poi_id":
        payload["group"][0].pop("poi_id")
    elif corruption == "number":
        payload["group"][0]["mean_score"] = "PRIVATE_BAD_SCORE"
    elif corruption == "duplicate":
        payload["group"].append(deepcopy(payload["group"][0]))
    elif corruption == "conflict":
        payload["removed"].append({
            "poi_id": "kakao:1",
            "rule": "member_exclusion",
            "detail": "PRIVATE_CONFLICT",
        })
    else:
        payload["score_scale"] = "PRIVATE_BAD_SCALE"

    run.response = payload
    db.commit()

    with pytest.raises(HTTPException) as error:
        read_evidence(evidence_state, ["kakao:1"])

    assert error.value.status_code == 502
    assert error.value.detail == "invalid_recommendation_evidence"
    assert "PRIVATE_" not in str(error.value.detail)
    
def test_read_does_not_write_or_call_services(evidence_state, monkeypatch):
    db, owner, _, _, _, run = evidence_state
    saved_response = deepcopy(run.response)

    def forbidden_service_call(*args, **kwargs):
        pytest.fail("A read tool must not call computation services.")

    for name in (
        "recommend",
        "build_plans",
        "check_availability",
        "agent_turn",
    ):
        monkeypatch.setattr(services, name, forbidden_service_call)

    owner.name = "Pending change"
    statements = []

    def allow_only_selects(
        connection, cursor, statement, parameters, context, executemany
    ):
        statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT"), statement

    event.listen(db.bind, "before_cursor_execute", allow_only_selects)

    try:
        read_evidence(evidence_state, ["kakao:1", "kakao:2"])

        assert statements
        assert owner in db.dirty
        assert run.response == saved_response
    finally:
        event.remove(db.bind, "before_cursor_execute", allow_only_selects)
        db.rollback()
        
