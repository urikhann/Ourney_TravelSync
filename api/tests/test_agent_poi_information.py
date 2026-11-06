"""Dataset, evidence and access tests for the POI read tool."""

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.agent.read_tools import get_poi_information
from app.agent.schemas import PoiInformationInput
from app.models import DatasetVersion, Poi

from .test_agent_read_context import make_run, state


def make_poi(
    version,
    poi_id,
    name,
    *,
    cost=None,
    cost_status="UNKNOWN",
    indoor=True,
):
    """Build a synthetic POI for these tests."""

    return Poi(
        id=poi_id,
        dataset_version=version,
        name=name,
        category="Cafe",
        lat=35.1,
        lng=129.1,
        source="synthetic-test",
        indoor=indoor,
        avg_cost_krw=cost,
        duration_min=60,
        features={"cafe": 1},
        field_status={
            "cost": cost_status,
            "duration": "ESTIMATED",
        },
        enriched_metadata={"internal_note": "PRIVATE_METADATA"},
    )


@pytest.fixture
def poi_state(state):
    """An archived dataset and a newer active dataset."""

    db, owner, outsider, trip, other_trip = state

    db.add_all([
        DatasetVersion(
            version="test-dataset",
            status="ARCHIVED",
            feature_schema_version="fs-1.0",
            mapping_version="rule_v2",
        ),
        DatasetVersion(
            version="new-dataset",
            status="ACTIVE",
            feature_schema_version="fs-1.0",
            mapping_version="rule_v2",
        ),
    ])
    db.commit()

    db.add_all([
        make_poi(
            "test-dataset",
            "kakao:1",
            "Archived Cafe",
        ),
        make_poi(
            "new-dataset",
            "kakao:1",
            "Current Cafe",
            cost=0,
            cost_status="KNOWN",
            indoor=False,
        ),
        make_poi(
            "new-dataset",
            "kakao:2",
            "New-only Cafe",
            cost=5000,
            cost_status="UNKNOWN",
        ),
    ])
    db.commit()

    return db, owner, outsider, trip, other_trip


def test_active_dataset_is_used_when_trip_has_no_run(poi_state):
    db, owner, _, trip, _ = poi_state

    result = get_poi_information(
        PoiInformationInput(trip_id=trip.id, poi_ids=["kakao:1"]),
        user=owner,
        db=db,
    )

    assert result.run_id is None
    assert result.dataset_basis == "ACTIVE_DATASET"
    assert result.dataset_version == "new-dataset"
    assert result.places[0].name == "Current Cafe"


@pytest.mark.parametrize("explicit_run", [False, True])
def test_saved_run_pins_dataset_without_fallback(poi_state, explicit_run):
    db, owner, _, trip, _ = poi_state
    run = make_run(db, trip.id)

    result = get_poi_information(
        PoiInformationInput(
            trip_id=trip.id,
            run_id=run.id if explicit_run else None,
            poi_ids=["kakao:1", "kakao:2"],
        ),
        user=owner,
        db=db,
    )

    assert result.run_id == run.id
    assert result.dataset_basis == "RECOMMENDATION_RUN"
    assert result.dataset_version == "test-dataset"
    assert result.places[0].name == "Archived Cafe"
    assert result.missing_poi_ids == ["kakao:2"]


def test_evidence_labels_preserve_uncertainty_and_zero(poi_state):
    db, owner, _, trip, _ = poi_state

    current = get_poi_information(
        PoiInformationInput(trip_id=trip.id, poi_ids=["kakao:1"]),
        user=owner,
        db=db,
    ).places[0]

    assert current.cost_krw.value == 0
    assert current.cost_krw.status == "REPORTED"
    assert current.indoor.value is False
    assert current.indoor.status == "UNVERIFIED"

    run = make_run(db, trip.id)

    archived = get_poi_information(
        PoiInformationInput(
            trip_id=trip.id,
            run_id=run.id,
            poi_ids=["kakao:1"],
        ),
        user=owner,
        db=db,
    ).places[0]

    assert archived.cost_krw.value is None
    assert archived.cost_krw.status == "UNKNOWN"
    assert archived.indoor.value is True
    assert archived.indoor.status == "UNVERIFIED"
    assert archived.duration_min.value == 60
    assert archived.duration_min.status == "ESTIMATED"

    for item in (current, archived):
        for evidence in (item.indoor, item.cost_krw, item.duration_min):
            assert evidence.verified_at is None


def test_requested_order_and_missing_ids_are_preserved(poi_state):
    db, owner, _, trip, _ = poi_state

    result = get_poi_information(
        PoiInformationInput(
            trip_id=trip.id,
            poi_ids=["kakao:2", "kakao:1", "missing:123"],
        ),
        user=owner,
        db=db,
    )

    assert [place.poi_id for place in result.places] == [
        "kakao:2",
        "kakao:1",
    ]
    assert result.missing_poi_ids == ["missing:123"]

    uncertain_cost = result.places[0].cost_krw
    assert uncertain_cost.value == 5000
    assert uncertain_cost.status == "UNVERIFIED"
    

@pytest.mark.parametrize("case", ["nonmember", "foreign_run"])
def test_unauthorized_scope_is_rejected(poi_state, case):
    db, owner, outsider, trip, other_trip = poi_state

    if case == "nonmember":
        user = outsider
        run_id = None
        expected_detail = "trip_not_found"
    else:
        user = owner
        run_id = make_run(db, other_trip.id).id
        expected_detail = "recommendation_run_not_found"

    with pytest.raises(HTTPException) as error:
        get_poi_information(
            PoiInformationInput(
                trip_id=trip.id,
                run_id=run_id,
                poi_ids=["kakao:1"],
            ),
            user=user,
            db=db,
        )

    assert error.value.status_code == 404
    assert error.value.detail == expected_detail


def test_missing_active_dataset_returns_clear_error(state):
    db, owner, _, trip, _ = state

    with pytest.raises(HTTPException) as error:
        get_poi_information(
            PoiInformationInput(trip_id=trip.id, poi_ids=["kakao:1"]),
            user=owner,
            db=db,
        )

    assert error.value.status_code == 503
    assert error.value.detail == "no_active_dataset"


def test_private_records_are_not_forwarded(poi_state):
    db, owner, _, trip, _ = poi_state
    make_run(db, trip.id)

    result = get_poi_information(
        PoiInformationInput(trip_id=trip.id, poi_ids=["kakao:1"]),
        user=owner,
        db=db,
    )
    output = result.model_dump_json()

    for private_value in (
        "PRIVATE_METADATA",
        "PRIVATE_REQUEST",
        "PRIVATE_RESPONSE",
        "PRIVATE_EXCLUSION",
        owner.email,
        owner.password_hash,
        trip.invite_code,
    ):
        assert private_value not in output


def test_poi_read_does_not_flush_or_write(poi_state):
    db, owner, _, trip, _ = poi_state
    owner.name = "Pending change"
    statements = []

    def allow_only_selects(
        connection, cursor, statement, parameters, context, executemany
    ):
        statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT"), statement

    event.listen(db.bind, "before_cursor_execute", allow_only_selects)

    try:
        get_poi_information(
            PoiInformationInput(trip_id=trip.id, poi_ids=["kakao:1"]),
            user=owner,
            db=db,
        )

        assert statements
        assert owner in db.dirty
    finally:
        event.remove(db.bind, "before_cursor_execute", allow_only_selects)
        db.rollback()
        
