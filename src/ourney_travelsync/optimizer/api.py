"""Public orchestration: validate snapshot → E2 → H → independent E3 gate."""
import hashlib
import json
from time import monotonic

from ortools import __version__ as ortools_version

from .contracts import Attempt, PlanRequest, PlanResponse, PlanRunRequest
from .eligibility import check_eligibility, common_bounds
from .solver import solve_prepared
from .validation import validate_final


def optimize(request: PlanRequest | dict) -> PlanResponse:
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
        return PlanResponse(**base, error_code=codes.get(result.status, "SOLVER_FAILED"))
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
        allocated = sum(p.allocated_seconds for p in first.phases)
        remaining = max(0, min(cap - allocated, deadline - monotonic()))
        if remaining > 0:
            selected = optimize(run.expanded.model_copy(update={"search_seconds": remaining}))
            results.append(selected)
        else:
            selected = first.model_copy(update={"solver_status": "UNKNOWN", "error_code": "SEARCH_TIMEOUT"})
    attempts = tuple(Attempt(input_snapshot=r.input_snapshot, solver_status=r.solver_status,
                             validation_status=r.validation_status, error_code=r.error_code,
                             eligibility=r.eligibility, phases=r.phases) for r in results)
    metrics = {**selected.metrics, "run_search_cap_seconds": cap,
               "run_allocated_search_seconds": sum(p.allocated_seconds for r in results for p in r.phases),
               "run_search_seconds": sum(p.wall_seconds for r in results for p in r.phases)}
    return selected.model_copy(update={"attempts": attempts, "metrics": metrics})
