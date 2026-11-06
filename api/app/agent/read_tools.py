"""Read-only tools for the TravelSync agent."""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from ..import_places import active_version
from ..models import Poi, TripMember, User
from ..places import place_dict
from .access import authorize_read_scope
from .schemas import (
    BooleanEvidence,
    IntegerEvidence,
    PoiInformationInput,
    PoiInformationItem,
    PoiInformationOutput,
    RunSummaryOutput,
    TripContextInput,
    TripContextOutput,
    RecommendationEvidenceInput,
    RecommendationEvidenceItem,
    RecommendationEvidenceOutput,
)

def get_trip_context(
    request: TripContextInput,
    *,
    user: User,
    db: Session,
) -> TripContextOutput: 
    """Read basic trip context and saved recommendation metadata."""
    
    with db.no_autoflush:
        trip, run = authorize_read_scope(
            trip_id=request.trip_id,
            run_id=request.run_id,
            user=user,
            db=db,
        )
        
        member_count = db.scalar(
        select(func.count())
        .select_from(TripMember)
        .where(TripMember.trip_id == trip.id)
        )
        
        recommendation = None
        if run is not None: 
            recommendation = RunSummaryOutput(
                run_id = run.id,
                created_at = run.created_at,
                model_version = run.model_version,
                method = run.method,
                dataset_version = run.dataset_version,
                feature_schema_version = run.feature_schema_version,
            )

        return TripContextOutput(
            trip_id=trip.id,
            name = trip.name,
            city = trip.city,
            start_date = trip.start_date,
            end_date = trip.end_date,
            member_count=member_count,
            recommendation=recommendation,
        )

def _integer_evidence(
    value: int | None,
    stored_status: str | None,
) -> IntegerEvidence:
    """Describe a numeric value without inventing verification."""

    if value is None:
        return IntegerEvidence(
            value=None,
            status="UNKNOWN",
            note="No value is stored",
        )
    
    if stored_status == "ESTIMATED":
        return IntegerEvidence(
            value=value,
            status="ESTIMATED",
            note="The application marks this value as estimated.",
        )

    if stored_status == "KNOWN":
        return IntegerEvidence(
            value=value,
            status="REPORTED",
            note=(
                "The application marks this value as known. "
                "Independent verification is not supplied by this tool."
            ),
        )

    return IntegerEvidence(
        value=value,
        status="UNVERIFIED",
        note="A value is stored, but its supporting status is insufficient.",
    )
    
def get_poi_information(
    request: PoiInformationInput,
    *,
    user: User,
    db: Session,
) -> PoiInformationOutput:
    """Read requested POIs with conservative evidence labels."""

    with db.no_autoflush:
        _, run = authorize_read_scope(
            trip_id=request.trip_id,
            run_id=request.run_id,
            user=user,
            db=db,
        )

        if run is not None:
            version = run.dataset_version
            dataset_basis = "RECOMMENDATION_RUN"
        else:
            version = active_version(db)
            dataset_basis = "ACTIVE_DATASET"

        if not version:
            raise HTTPException(503, "no_active_dataset")

        rows = db.scalars(
            select(Poi).where(
                Poi.dataset_version == version,
                Poi.id.in_(request.poi_ids),
            )
        ).all()

        by_id = {poi.id: poi for poi in rows}
        places = []
        missing = []

        for poi_id in request.poi_ids:
            poi = by_id.get(poi_id)

            if poi is None:
                missing.append(poi_id)
                continue

            view = place_dict(poi)
            statuses = poi.field_status or {}

            indoor_value = view["indoor"]
            if indoor_value is None:
                indoor = BooleanEvidence(
                    value=None,
                    status="UNKNOWN",
                    note="Indoor or outdoor status is not stored.",
                )
            else:
                indoor = BooleanEvidence(
                    value=indoor_value,
                    status="UNVERIFIED",
                    note=(
                        "This is a stored flag. CSV imports may infer it "
                        "from Features; field-specific verification is "
                        "not supplied by this tool."
                    ),
                )

            places.append(
                PoiInformationItem(
                    poi_id=view["id"],
                    name=view["name"],
                    category=view["category"],
                    record_source=poi.source,
                    record_updated_at=poi.updated_at,
                    indoor=indoor,
                    cost_krw=_integer_evidence(
                        view["avg_cost_krw"],
                        statuses.get("cost"),
                    ),
                    duration_min=_integer_evidence(
                        view["duration_min"],
                        statuses.get("duration"),
                    ),
                )
            )

        return PoiInformationOutput(
            trip_id=request.trip_id,
            run_id=run.id if run is not None else None,
            dataset_version=version,
            dataset_basis=dataset_basis,
            places=places,
            missing_poi_ids=missing,
        )
        
def _saved_evidence_rows(response: dict, section: str) -> list[dict]:
    """Read a result section without accepting malformed row shapes."""

    rows = response.get(section, [])

    if not isinstance(rows, list):
        raise HTTPException(502, "invalid_recommendation_evidence")

    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(502, "invalid_recommendation_evidence")

        poi_id = row.get("poi_id")
        if not isinstance(poi_id, str) or not poi_id.strip():
            raise HTTPException(502, "invalid_recommendation_evidence")

    return rows

def get_recommendation_evidence(
    request: RecommendationEvidenceInput,
    *,
    user: User,
    db: Session,
) -> RecommendationEvidenceOutput:
    """Project public evidence from a saved Run without recalculation."""

    with db.no_autoflush:
        _, run = authorize_read_scope(
            trip_id=request.trip_id,
            run_id=request.run_id,
            user=user,
            db=db,
        )

        if run is None:
            raise HTTPException(404, "recommendation_run_not_found")

        response = run.response
        if not isinstance(response, dict):
            raise HTTPException(502, "invalid_recommendation_evidence")

        group = _saved_evidence_rows(response, "group")
        removed = _saved_evidence_rows(response, "removed")
        items = []

        try:
            for poi_id in request.poi_ids:
                selected_rows = [
                    row for row in group if row["poi_id"] == poi_id
                ]
                removed_rows = [
                    row for row in removed if row["poi_id"] == poi_id
                ]

                if len(selected_rows) > 1 or (
                    selected_rows and removed_rows
                ):
                    raise HTTPException(
                        502, "invalid_recommendation_evidence"
                    )

                if selected_rows:
                    row = selected_rows[0]

                    item = RecommendationEvidenceItem(
                        poi_id=poi_id,
                        state="SELECTED",
                        source_section="group",
                        group_rank=row.get("group_rank"),
                        mean_score=row.get("mean_score"),
                        selection_gain=row.get("selection_gain"),
                        group_utility=row.get("group_utility"),
                    )

                elif removed_rows:
                    codes = []
                    code_map = {
                        "member_exclusion": "MEMBER_EXCLUSION",
                        "misery": "MISERY_THRESHOLD",
                        "no_features": "NO_FEATURES",
                    }

                    for row in removed_rows:
                        rule = row.get("rule")
                        code = (
                            code_map.get(rule, "OTHER_RECORDED_EXCLUSION")
                            if isinstance(rule, str)
                            else "OTHER_RECORDED_EXCLUSION"
                        )

                        if code not in codes:
                            codes.append(code)

                    item = RecommendationEvidenceItem(
                        poi_id=poi_id,
                        state="EXCLUDED",
                        source_section="removed",
                        exclusion_codes=codes,
                    )

                else:
                    item = RecommendationEvidenceItem(
                        poi_id=poi_id,
                        state="NOT_RECORDED",
                    )

                items.append(item)

            return RecommendationEvidenceOutput(
                trip_id=request.trip_id,
                run=RunSummaryOutput(
                    run_id=run.id,
                    created_at=run.created_at,
                    model_version=run.model_version,
                    method=run.method,
                    dataset_version=run.dataset_version,
                    feature_schema_version=run.feature_schema_version,
                ),
                score_scale=response.get("score_scale"),
                individual_method=response.get("individual_method"),
                scorer_version=response.get("scorer_version"),
                items=items,
            )

        except ValidationError:
            raise HTTPException(
                502, "invalid_recommendation_evidence"
            ) from None
            
