"""Public orchestration: validate snapshot → E2 → H → independent E3 gate."""
import hashlib
import json
from time import monotonic

from ortools import __version__ as ortools_version

from .contracts import Attempt, PlanRequest, PlanResponse, PlanRunRequest
from .eligibility import check_eligibility, common_bounds
from .solver import solve_prepared
from .validation import validate_final
from .meals import effective_meals
from .waiting import gap_retry_request, improves_gap


def _optimize_once(request: PlanRequest | dict) -> PlanResponse:
    # Serialize/reparse to take ownership of the nested snapshot and reject
    # unchecked model_copy values. No callers' inputs are mutated.
    req = PlanRequest.model_validate(request.model_dump(mode="json") if isinstance(request, PlanRequest) else request)
    req = req.model_copy(deep=True)
    eligibility = check_eligibility(req)
    base = dict(run_id=req.run_id, input_snapshot=req, eligibility=eligibility)
    if len(common_bounds(req)) != len(req.days):
        return PlanResponse(**base, solver_status="NOT_RUN", error_code="CONSTRAINT_CONFLICT")
    if any(not any(d.date in e.feasible_dates for e in eligibility) for d in req.days):
        return PlanResponse(**base, solver_status="NOT_RUN", error_code="INSUFFICIENT_CANDIDATES")
    result = solve_prepared(req, eligibility)
    base.update(solver_status=result.status, phases=result.phases)
    if not result.days:
        codes = {"INFEASIBLE": "NO_FEASIBLE_ITINERARY", "UNKNOWN": "SEARCH_TIMEOUT",
                 "MODEL_INVALID": "INVALID_SOLVER_MODEL"}
        conflicts = tuple(f"{d.date}:{m.name}:REQUIRED_MEAL_UNSCHEDULED:{m.start_min}-{m.end_min}"
                          for d in req.days
                          for m in (effective_meals(d, req.daily_composition) if req.daily_composition else ())
                          if m.required) if result.status == "INFEASIBLE" else ()
        return PlanResponse(**base, error_code=codes.get(result.status, "SOLVER_FAILED"),
                            validation_errors=conflicts)
    if req.provisional:
        return PlanResponse(**base, days=result.days)
    validation = validate_final(req, result.days)
    if not validation.passed:
        return PlanResponse(**base, validation_status="FAILED", error_code="FINAL_VALIDATION_FAILED",
                            validation_errors=validation.errors)
    snapshot = json.dumps(req.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    metrics = {**validation.metrics, "solver_version": ortools_version, "optimizer_version": "1.0",
               "snapshot_sha256": hashlib.sha256(snapshot.encode()).hexdigest(),
               "solver_workers": 1, "seed": req.seed,
               "search_seconds": sum(p.wall_seconds for p in result.phases),
               "allocated_search_seconds": sum(p.allocated_seconds for p in result.phases)}
    return PlanResponse(**base, validation_status="PASSED", days=result.days, metrics=metrics)


def optimize(request: PlanRequest | dict) -> PlanResponse:
    req = PlanRequest.model_validate(request.model_dump(mode="json") if isinstance(request, PlanRequest) else request)
    if not req.gap_policy.enabled or req.provisional or req.gap_target:
        return _optimize_once(req)
    deadline = monotonic() + req.search_seconds
    first = _optimize_once(req.model_copy(update={"search_seconds": req.search_seconds * .8}))
    if first.validation_status != "PASSED":
        return first.model_copy(update={"input_snapshot": req})
    results = [first]
    selected = first
    remaining = max(0, min(req.search_seconds * .2, deadline - monotonic()))
    if remaining > 0:
        retry = gap_retry_request(req, first, remaining)
        if retry is not None:
            alternative = _optimize_once(retry)
            results.append(alternative)
            if improves_gap(req, first, alternative) and validate_final(req, alternative.days).passed:
                selected = alternative
    attempts = tuple(Attempt(input_snapshot=r.input_snapshot, solver_status=r.solver_status,
                             validation_status=r.validation_status, error_code=r.error_code,
                             eligibility=r.eligibility, phases=r.phases) for r in results)
    metrics = {**selected.metrics, "gap_fill_attempted": len(results) > 1,
               "gap_fill_applied": selected is not first,
               "search_seconds": sum(p.wall_seconds for r in results for p in r.phases),
               "allocated_search_seconds": sum(p.allocated_seconds for r in results for p in r.phases)}
    snapshot = json.dumps(req.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    metrics["snapshot_sha256"] = hashlib.sha256(snapshot.encode()).hexdigest()
    return selected.model_copy(update={"input_snapshot": req, "attempts": attempts, "metrics": metrics})


def optimize_run(request: PlanRunRequest | dict) -> PlanResponse:
    """One optional pre-enriched expansion within the SAME total search budget.

    Supplying snapshots does not invoke production enrichment or change GFAR.
    The upstream orchestrator owns obtaining K=30 then at most K=60 candidates.
    Both attempts' complete inputs/E2 results/raw phase states remain replayable.
    """
    run = PlanRunRequest.model_validate(request.model_dump(mode="json")
                                        if isinstance(request, PlanRunRequest) else request)
    cap = run.initial.search_seconds
    deadline = monotonic() + cap
    first_cap = cap if run.expanded is None else cap / 2
    first = optimize(run.initial.model_copy(update={"search_seconds": first_cap}))
    results = [first]
    selected = first
    if (run.expanded is not None and first.error_code in
            {"INSUFFICIENT_CANDIDATES", "NO_FEASIBLE_ITINERARY", "SEARCH_TIMEOUT"}):
        allocated = first.metrics.get("allocated_search_seconds", sum(p.allocated_seconds for p in first.phases))
        remaining = max(0, min(cap - allocated, deadline - monotonic()))
        if remaining > 0:
            selected = optimize(run.expanded.model_copy(update={"search_seconds": remaining}))
            results.append(selected)
        else:
            selected = first.model_copy(update={"solver_status": "UNKNOWN", "error_code": "SEARCH_TIMEOUT"})
    attempts = []
    for result in results:
        attempts.extend(result.attempts or (Attempt(
            input_snapshot=result.input_snapshot, solver_status=result.solver_status,
            validation_status=result.validation_status, error_code=result.error_code,
            eligibility=result.eligibility, phases=result.phases),))
    metrics = {**selected.metrics, "run_search_cap_seconds": cap,
               "run_allocated_search_seconds": sum(p.allocated_seconds for a in attempts for p in a.phases),
               "run_search_seconds": sum(p.wall_seconds for a in attempts for p in a.phases)}
    return selected.model_copy(update={"attempts": tuple(attempts), "metrics": metrics})
