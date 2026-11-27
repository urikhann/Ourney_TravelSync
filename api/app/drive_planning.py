"""Draft a compact route, then verify and repair using only selected road legs."""
from math import ceil

from fastapi import HTTPException
from pydantic import ValidationError

from ourney_travelsync.geography import haversine_km
from ourney_travelsync.optimizer.contracts import Attempt, Edge, PlanResponse
from ourney_travelsync.optimizer.eligibility import check_eligibility
from ourney_travelsync.optimizer.meals import breakfast_windows, effective_meals
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.optimizer.waiting import gap_retry_request, improves_gap, waiting_gaps
from ourney_travelsync.place_identity import candidate_identity

from . import services
from .optimization import PlanningSnapshot, fingerprint, solver_request


MAX_ROUTE_ROUNDS = 3
MAX_GAP_CANDIDATES = 3
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


def gap_route_legs(req, result, estimates):
    """Shortlist unused activities geographically, then verify their road legs."""
    selected = {v.poi_id for day in result.days for v in day.items}
    selected_identities = {candidate_identity(p) for p in req.candidates if p.poi_id in selected}
    evidence = {edge_key(e): e for e in req.edges}
    draft_routes = {**estimates, **evidence}
    draft = req.model_copy(update={"provisional": True, "edges": tuple(draft_routes.values())})
    eligible = {e.poi_id: e for e in check_eligibility(draft)}
    gaps = sorted(waiting_gaps(req, result.days), key=lambda g: g["unallocated_minutes"], reverse=True)
    candidates = sorted(req.candidates, key=lambda p: (-p.group_utility, -p.mean_score, p.group_rank, p.poi_id))
    for gap in gaps:
        if gap["action"] not in ("fill", "reoptimize"):
            continue
        day = next(d.date for d in result.days if d.date.isoformat() == gap["date"])
        direct = evidence.get((day, gap["after_poi_id"], gap["before_poi_id"]))
        if direct is None or not direct.usable:
            continue
        legs, shortlisted = [], 0
        for candidate in candidates:
            if (candidate_identity(candidate) in selected_identities or candidate.visit_category != "activity"
                    or day not in eligible[candidate.poi_id].feasible_dates):
                continue
            keys = ((day, gap["after_poi_id"], candidate.poi_id),
                    (day, candidate.poi_id, gap["before_poi_id"]))
            routes = [evidence.get(key, estimates.get(key)) for key in keys]
            if any(e is None or not (e.usable or e.draft_usable) for e in routes):
                continue
            if max(e.duration_minutes for e in routes) > req.gap_policy.nearby_leg_minutes:
                continue
            # Draft travel times may overestimate actual roads. Only reject
            # an activity whose stay alone cannot fit before fetching evidence.
            if candidate.duration_min.value > gap["duration_minutes"] + direct.duration_minutes:
                continue
            legs.extend(key for key in keys if key not in evidence)
            shortlisted += 1
            if shortlisted == MAX_GAP_CANDIDATES:
                break
        if legs:
            return list(dict.fromkeys(legs))
    return []


async def fill_driving_gap(baseline, context, estimates, seconds, audit, db):
    req, response, result, operational = baseline
    if not req.gap_policy.enabled or seconds <= 0 or not validate_final(req, result.days).passed:
        return baseline
    gaps = [g for g in waiting_gaps(req, result.days) if g["action"] in ("fill", "reoptimize")]
    if not gaps:
        return baseline
    legs = gap_route_legs(req, result, estimates)
    log = {"search_cap_seconds": seconds, "gaps": gaps, "requested_legs": [], "status": "RETAINED"}
    audit.planning_data = {**audit.planning_data, "gap_filling": log}
    db.commit()
    enriched = req.model_copy(update={"search_seconds": req.search_seconds + seconds})
    extra = operational
    try:
        if legs:
            log["requested_legs"] = [dict(date=day.isoformat(), origin=origin, destination=destination)
                                     for day, origin, destination in legs]
            request, route_response, data, spent = await routing_snapshot(
                context, legs, operational.get("routing_attempts", {}), MAX_ROUTE_ROUNDS)
            log.update(requested_legs=request["route_legs"], route_response=route_response)
            if data.routing_version != req.routing_version:
                raise HTTPException(502, "planning_data_invalid_response")
            evidence = {edge_key(e): e for e in req.edges}
            evidence.update((edge_key(e), e) for e in data.edges)
            enriched = enriched.model_copy(update={"edges": tuple(evidence.values())})
            extra = {**operational, "edges": [e.model_dump(mode="json") for e in enriched.edges],
                     "routing_attempts": spent,
                     "routing_warnings": sorted(set(operational.get("routing_warnings", [])) | set(data.routing_warnings))}
        retry = gap_retry_request(enriched, result, seconds)
        if retry is not None:
            log["optimizer_request"] = retry.model_dump(mode="json")
            retry_response, alternative = await solve_checked(retry)
            log["response"] = retry_response
            if (improves_gap(enriched, result, alternative)
                    and validate_final(retry, alternative.days).passed
                    and validate_final(enriched, alternative.days).passed):
                attempt = Attempt(input_snapshot=retry, solver_status=alternative.solver_status,
                                  validation_status=alternative.validation_status, error_code=alternative.error_code,
                                  eligibility=alternative.eligibility, phases=alternative.phases)
                # Internal repair locks stay in the replay attempt. The public
                # snapshot retains only the group's requested locks.
                metrics = {**alternative.metrics, "gap_fill_attempted": True, "gap_fill_applied": True,
                           "search_seconds": result.metrics.get("search_seconds", 0) + alternative.metrics.get("search_seconds", 0),
                           "allocated_search_seconds": result.metrics.get("allocated_search_seconds", 0) + alternative.metrics.get("allocated_search_seconds", 0),
                           "snapshot_sha256": fingerprint(enriched.model_dump(mode="json"))}
                accepted = alternative.model_copy(update={"input_snapshot": enriched,
                                                          "attempts": (*result.attempts, attempt), "metrics": metrics})
                response = accepted.model_dump(mode="json")
                baseline = enriched, response, accepted, extra
                log["status"] = "APPLIED"
    except HTTPException as exc:
        log["error_code"] = exc.detail
    if log["status"] != "APPLIED":
        baseline = req, response, result, {**operational,
            "routing_attempts": extra.get("routing_attempts", operational.get("routing_attempts", {})),
            "routing_warnings": extra.get("routing_warnings", operational.get("routing_warnings", []))}
    audit.optimizer_request = baseline[0].model_dump(mode="json")
    audit.planning_data = {**audit.planning_data, "response": baseline[3], "gap_filling": log}
    db.commit()
    return baseline


async def plan_drive(run, body, prepared, context, audit, db, *, request_transform=None, initial_attempts=None):
    # The operating snapshot is read once for optimization. Individual routing
    # rounds only contribute route evidence and keep its provenance.
    initial, operating, _, spent = await routing_snapshot(context, [], initial_attempts or {}, 0)
    rounds = []
    audit.planning_data = {"request": initial, "response": operating, "routing_rounds": []}
    db.commit()
    try:
        base = solver_request(run, audit.id, body, prepared, initial, operating)
        if request_transform is not None:
            base = request_transform(base)
    except (ValidationError, ValueError, TypeError, KeyError):
        raise HTTPException(502, "planning_data_invalid_response") from None
    estimates = {edge_key(edge): edge for edge in draft_edges(base, context)}
    evidence = {}
    warnings = set()
    last_result = None
    best_result = None
    best_optional_count = -1
    allocated_search = 0
    draft_composition = base.daily_composition
    fallback_composition = None
    if draft_composition is not None and not draft_composition.breakfast_require_verified:
        if draft_composition.breakfast_enabled or any(w.name == "breakfast" for w in draft_composition.meal_windows):
            fallback_composition = draft_composition.model_copy(update={
                "breakfast_enabled": False,
                "meal_windows": tuple(w for w in draft_composition.meal_windows if w.name != "breakfast"),
                "meal_overrides": tuple(o for o in draft_composition.meal_overrides if o.name != "breakfast"),
            })
    if draft_composition is not None and draft_composition.evening_enabled:
        fallback_composition = (fallback_composition or draft_composition).model_copy(update={"evening_enabled": False})
    automatic_evening = {p.poi_id for p in base.candidates if p.visit_category == "evening"
                         and not any(l.poi_id == p.poi_id for l in base.locked_items)}

    def optional_visit(item):
        return bool((item.breakfast_status is not None and base.daily_composition is not None
                     and not base.daily_composition.breakfast_require_verified)
                    or (base.daily_composition is not None and base.daily_composition.evening_enabled
                        and item.poi_id in automatic_evening))
    # Give the initial search half the total time. Split the other half across
    # the two possible repair rounds.
    for round_index in range(MAX_ROUTE_ROUNDS):
        round_seconds = base.search_seconds * (.5 if round_index == 0 else .25)
        provisional = tuple(
            evidence[key].model_copy(update={"straight_line_m": estimate.straight_line_m})
            if key in evidence else estimate for key, estimate in estimates.items()
            if key not in evidence or evidence[key].usable)
        # Reserve a route that can work without optional breakfast/evening stops.
        if round_index == MAX_ROUTE_ROUNDS - 1 and fallback_composition is not None and not best_result:
            draft_composition = fallback_composition
        draft_req = base.model_copy(update={"provisional": True, "edges": provisional,
                                           "daily_composition": draft_composition,
                                           "search_seconds": round_seconds * .65})
        allocated_search += draft_req.search_seconds
        draft_response, draft = await solve_checked(draft_req)
        # Keep failed initial drafts replayable, including the stage and E2 reasons.
        # _generate may exit before the later verified-solve persistence path.
        audit.optimizer_request = draft_req.model_dump(mode="json")
        audit.response = draft_response
        audit.planning_data = {**audit.planning_data, "stage": "draft"}
        db.commit()
        if draft.validation_status != "NOT_RUN":
            raise HTTPException(502, "optimizer_invalid_response")
        if not draft.days:
            if last_result:
                if fallback_composition is not None and draft_composition is not fallback_composition:
                    draft_composition = fallback_composition
                    continue
                break
            if draft.error_code in ("NO_FEASIBLE_ITINERARY", "INSUFFICIENT_CANDIDATES") and missing_required_breakfast(draft_req, draft):
                raise HTTPException(409, "optimizer_breakfast_unavailable")
            raise HTTPException(409, "optimizer_" + (draft.error_code or "invalid_result").lower())
        legs = list(chosen_edges(draft.days))
        if fallback_composition is not None:
            # Verify a path around optional stops too. A slower provider
            # leg can otherwise invalidate the draft before the solver can skip it.
            for day in draft.days:
                remaining = tuple(item for item in day.items if not optional_visit(item))
                if remaining and len(remaining) < len(day.items):
                    legs.extend(chosen_edges((day.model_copy(update={"items": remaining}),)))
        legs = [key for key in dict.fromkeys(legs) if key not in evidence]
        if not legs:
            if fallback_composition is not None and draft_composition is not fallback_composition:
                draft_composition = fallback_composition
                continue
            break
        try:
            request, _, data, spent = await routing_snapshot(context, legs, spent, round_index)
            if data.routing_version != base.routing_version:
                raise HTTPException(502, "planning_data_invalid_response")
        except HTTPException:
            if best_result is None:
                raise
            break
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
        allocated_search += verified.search_seconds
        audit.optimizer_request = verified.model_dump(mode="json")
        db.commit()
        response, result = await solve_checked(verified)
        audit.response = response
        audit.planning_data = {**audit.planning_data, "stage": "verified"}
        db.commit()
        last_result = verified, response, result, operational
        if result.validation_status == "PASSED":
            count = sum(optional_visit(item) for day in result.days for item in day.items)
            if count > best_optional_count:
                best_result, best_optional_count = last_result, count
            draft_count = sum(optional_visit(item) for day in draft.days for item in day.items)
            if fallback_composition is None or count >= draft_count:
                break
        elif result.error_code not in ("INSUFFICIENT_CANDIDATES", "NO_FEASIBLE_ITINERARY", "SEARCH_TIMEOUT"):
            break
    if best_result is not None and best_result is not last_result:
        audit.optimizer_request = best_result[0].model_dump(mode="json")
        audit.planning_data = {**audit.planning_data, "response": best_result[3]}
    if last_result is None:
        raise HTTPException(502, "optimizer_invalid_response")
    selected = best_result or last_result
    if selected[2].validation_status == "PASSED":
        remaining = max(0, min(base.search_seconds * .2, base.search_seconds - allocated_search))
        selected = await fill_driving_gap(selected, context, estimates, remaining, audit, db)
    audit.planning_data = {**audit.planning_data, "stage": "verified"}
    return selected
