"""Authenticated inspection endpoint for the read-only agent tools."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from .. import config
from ..agent.read_tools import (
    get_poi_information,
    get_recommendation_evidence,
    get_trip_context,
)
from ..agent.schemas import (
    AgentContextOutput,
    AgentContextQuery,
    PoiInformationInput,
    RecommendationEvidenceInput,
    TripContextInput,
)
from ..db import get_db
from ..models import User
from .auth import current_user


def require_agent_enabled() -> None:
    if not config.FEATURE_AGENT:
        raise HTTPException(404, "agent_disabled")


router = APIRouter(
    tags=["agent"],
    dependencies=[Depends(require_agent_enabled)],
)


@router.get(
    "/trips/{trip_id}/agent/context",
    response_model=AgentContextOutput,
)
def inspect_agent_context(
    trip_id: Annotated[
        str,
        Path(min_length=1, max_length=36, pattern=r".*\S.*"),
    ],
    query: Annotated[AgentContextQuery, Query()],
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AgentContextOutput:
    """Return a bounded evidence bundle without changing application state."""

    with db.no_autoflush:
        context = get_trip_context(
            TripContextInput(
                trip_id=trip_id,
                run_id=query.run_id,
            ),
            user=user,
            db=db,
        )

        result = AgentContextOutput(trip_context=context)

        if not query.poi_ids:
            return result

        resolved_run_id = (
            context.recommendation.run_id
            if context.recommendation is not None
            else None
        )

        poi_information = get_poi_information(
            PoiInformationInput(
                trip_id=trip_id,
                run_id=resolved_run_id,
                poi_ids=query.poi_ids,
            ),
            user=user,
            db=db,
        )

        if poi_information.run_id != resolved_run_id:
            raise HTTPException(409, "agent_context_changed")

        result.poi_information = poi_information

        if resolved_run_id is not None:
            result.recommendation_evidence = get_recommendation_evidence(
                RecommendationEvidenceInput(
                    trip_id=trip_id,
                    run_id=resolved_run_id,
                    poi_ids=query.poi_ids,
                ),
                user=user,
                db=db,
            )

        return result
    
    