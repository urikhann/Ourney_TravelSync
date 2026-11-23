"""Draft a compact route, then verify and repair using only selected road legs."""
from math import ceil

from fastapi import HTTPException
from pydantic import ValidationError

from ourney_travelsync.geography import haversine_km
from ourney_travelsync.optimizer.contracts import Edge, PlanResponse
from ourney_travelsync.optimizer.meals import breakfast_windows, effective_meals

from . import services
from .optimization import PlanningSnapshot, fingerprint, solver_request


MAX_ROUTE_ROUNDS = 3
# A draft-only estimate of urban travel. The provider supplies the published times.
DRAFT_SPEED_KMH = 25
DRAFT_ROAD_FACTOR = 1.3


def edge_key(edge):
    return edge.date, edge.origin, edge.destination


def draft_edges(req, context):
    points = {p["poi_id"]: p for p in context["pois"]} | context["points"]
    coords = {pid: (p["latitude"], p["longitude"]) for pid, p in points.items()}
    for day in req.days:
        ids = context["day_candidate_ids"][day.date.isoformat()]
        for origin in (day.start_point, *ids):
            for destination in (*ids, day.end_point):
                if origin == destination or (origin == day.start_point and destination == day.end_point):
                    continue
                distance = haversine_km(coords[origin], coords[destination])
                meters = round(distance * 1000)
                yield Edge(date=day.date, origin=origin, destination=destination, status="ESTIMATED",
                           provider="haversine-draft-v1", distance_m=meters, straight_line_m=meters,
                           duration_minutes=min(1440, ceil(60 * distance * DRAFT_ROAD_FACTOR / DRAFT_SPEED_KMH)),
                           walk_minutes=0, group_fare=0)


def chosen_edges(days):
    for day in days:
        nodes = (day.start_point, *(item.poi_id for item in day.items), day.end_point)
        yield from ((day.date, origin, dest) for origin, dest in zip(nodes, nodes[1:]))


async def routing_snapshot(context, legs, attempts, round_index):
    request = {**context, "route_legs": [
        dict(date=day.isoformat(), origin=origin, destination=dest) for day, origin, dest in legs],
        "routing_attempts": attempts, "expand_routing_budget": round_index > 0}
    request["context_id"] = fingerprint({k: v for k, v in request.items() if k != "context_id"})
    response = await services.planning_data(request, selected=True)
    try:
        data = PlanningSnapshot.model_validate(response)
        if any(getattr(data, k) != request[k] for k in ("context_id", "dataset_version", "transport_mode")):
            raise ValueError
        if ({edge_key(edge) for edge in data.edges} != set(legs) or len(data.edges) != len(legs)
                or any(edge.status == "ESTIMATED" or edge.straight_line_m is not None for edge in data.edges)):
            raise ValueError
        spent = data.routing_attempts
        if spent is None or any(spent.get(endpoint, 0) < value for endpoint, value in attempts.items()):
            raise ValueError
    except (ValidationError, ValueError):
        raise HTTPException(502, "planning_data_invalid_response") from None
    return request, response, data, spent


async def solve_checked(req):
    response = await services.build_plans(req.model_dump(mode="json"))
    try:
        result = PlanResponse.model_validate(response)
    except ValidationError:
        raise HTTPException(502, "optimizer_invalid_response") from None
    if result.input_snapshot != req or result.run_id != req.run_id:
        raise HTTPException(502, "optimizer_snapshot_mismatch")
    return response, result


def missing_required_breakfast(req, result):
    policy = req.daily_composition
    if policy is None:
        return False
    eligible = {place.poi_id: place for place in result.eligibility}
    for day in req.days:
        required = next((meal for meal in effective_meals(day, policy)
                         if meal.name == "breakfast" and meal.required), None)
        if required is None:
            continue
        if not any(
            candidate.visit_category in ("meal", "cafe")
            and day.date in eligible[candidate.poi_id].feasible_dates
            and any(min(window.end_min, required.end_min, day.end_min)
                    - max(window.start_min, required.start_min, day.start_min) >= candidate.duration_min.value
                    for window in breakfast_windows(candidate, day.date))
            for candidate in req.candidates if candidate.poi_id in eligible
        ):
            return True
    return False


async def plan_drive(run, body, prepared, context, audit, db):
    # The operating snapshot is read once for optimization. Individual routing
    # rounds only contribute route evidence and keep its provenance.
    initial, operating, _, spent = await routing_snapshot(context, [], {}, 0)
    rounds = []
    audit.planning_data = {"request": initial, "response": operating, "routing_rounds": []}
    db.commit()
    try:
        base = solver_request(run, audit.id, body, prepared, initial, operating)
    except (ValidationError, ValueError, TypeError, KeyError):
        raise HTTPException(502, "planning_data_invalid_response") from None
    estimates = {edge_key(edge): edge for edge in draft_edges(base, context)}
    evidence = {}
    warnings = set()
    last_result = None
    # Give the initial search half the total time. Split the other half across
    # the two possible repair rounds.
    for round_index in range(MAX_ROUTE_ROUNDS):
        round_seconds = base.search_seconds * (.5 if round_index == 0 else .25)
        provisional = tuple(
            evidence[key].model_copy(update={"straight_line_m": estimate.straight_line_m})
            if key in evidence else estimate for key, estimate in estimates.items()
            if key not in evidence or evidence[key].usable)
        draft_req = base.model_copy(update={"provisional": True, "edges": provisional,
                                           "search_seconds": round_seconds * .65})
        _, draft = await solve_checked(draft_req)
        if draft.validation_status != "NOT_RUN":
            raise HTTPException(502, "optimizer_invalid_response")
        if not draft.days:
            if last_result:
                break
            if draft.error_code in ("NO_FEASIBLE_ITINERARY", "INSUFFICIENT_CANDIDATES") and missing_required_breakfast(draft_req, draft):
                raise HTTPException(409, "optimizer_breakfast_unavailable")
            raise HTTPException(409, "optimizer_" + (draft.error_code or "invalid_result").lower())
        legs = [key for key in chosen_edges(draft.days) if key not in evidence]
        if not legs:
            break
        request, _, data, spent = await routing_snapshot(context, legs, spent, round_index)
        if data.routing_version != base.routing_version:
            raise HTTPException(502, "planning_data_invalid_response")
        evidence.update((edge_key(edge), edge) for edge in data.edges)
        warnings.update(data.routing_warnings)
        rounds.append({"request_context_id": request["context_id"], "requested_legs": request["route_legs"],
                       "routing_attempts": spent, "edge_statuses": [edge.status for edge in data.edges],
                       "routing_warnings": list(data.routing_warnings)})
        operational = {**operating,
                       "edges": [edge.model_dump(mode="json") for edge in evidence.values()],
                       "routing_attempts": spent, "routing_warnings": sorted(warnings)}
        audit.planning_data = {"request": initial, "response": operational, "routing_rounds": list(rounds)}
        # A route is allowed in this solve only if the adapter returned it.
        verified = base.model_copy(update={"edges": tuple(evidence.values()),
                                           "search_seconds": round_seconds * .35})
        audit.optimizer_request = verified.model_dump(mode="json")
        db.commit()
        response, result = await solve_checked(verified)
        last_result = verified, response, result, operational
        if result.validation_status == "PASSED" or result.error_code not in (
                "INSUFFICIENT_CANDIDATES", "NO_FEASIBLE_ITINERARY", "SEARCH_TIMEOUT"):
            break
    if last_result is None:
        raise HTTPException(502, "optimizer_invalid_response")
    return last_result
