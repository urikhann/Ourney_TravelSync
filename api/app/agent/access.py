"""Authorization and scoped lookups shared by read-only tools."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import member_trip
from ..models import RecommendationRun, Trip, User


def authorize_read_scope(
    trip_id: str,
    run_id: int | None,
    *,
    user: User,
    db: Session,
) -> tuple[Trip, RecommendationRun | None]:
    """Check membership, then resolve a Run belonging to that trip."""

    with db.no_autoflush:
        trip = member_trip(trip_id=trip_id, user=user, db=db)

        query = select(RecommendationRun).where(
            RecommendationRun.trip_id == trip.id
        )

        if run_id is not None:
            query = query.where(RecommendationRun.id == run_id)

        query = query.order_by(RecommendationRun.id.desc()).limit(1)
        run = db.scalar(query)

        if run_id is not None and run is None:
            raise HTTPException(404, "recommendation_run_not_found")

        return trip, run
