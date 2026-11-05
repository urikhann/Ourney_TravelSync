"""Public orchestration: validate snapshot → E2 → H → independent E3 gate."""
import hashlib
import json

from ortools import __version__ as ortools_version

from .contracts import PlanRequest, PlanResponse
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
