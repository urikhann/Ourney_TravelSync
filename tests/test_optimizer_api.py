from copy import deepcopy

import pytest
pytest.importorskip("ortools")
from ortools.sat.python import cp_model
from pydantic import ValidationError

from optimizer_fixtures import request_data
from ourney_travelsync.optimizer import api
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.validation import ValidationResult


def test_success_keeps_snapshot_and_e3_metrics():
    data = request_data(3, 3)
    before = deepcopy(data)
    res = api.optimize(data)
    assert res.validation_status == "PASSED"
    assert res.solver_status == "OPTIMAL"
    assert len(res.phases) == 3
    assert res.metrics["allocated_search_seconds"] <= 2
    assert res.metrics["group_utility"] == 2700
    assert res.input_snapshot.dataset_version == "synthetic-v1"
    assert data == before
    replay = api.optimize(res.input_snapshot)
    assert replay.days == res.days
    assert replay.metrics["snapshot_sha256"] == res.metrics["snapshot_sha256"]


def test_zero_budget_returns_unknown_without_fabricated_plan():
    data = request_data()
    data["search_seconds"] = 0
    res = api.optimize(data)
    assert res.solver_status == "UNKNOWN"
    assert res.error_code == "SEARCH_TIMEOUT"
    assert not res.days


def test_infeasible_is_distinct_from_timeout():
    data = request_data(1, 2)
    res = api.optimize(data)
    assert res.solver_status == "INFEASIBLE"
    assert res.error_code == "NO_FEASIBLE_ITINERARY"


def test_missing_data_and_member_conflict_are_not_solver_infeasibility():
    data = request_data(1)
    data["candidates"][0]["duration_min"] = {}
    res = api.optimize(data)
    assert res.error_code == "INSUFFICIENT_CANDIDATES" and res.solver_status == "NOT_RUN"
    data = request_data(1)
    data["members"][0]["available"] = {}
    assert api.optimize(data).error_code == "CONSTRAINT_CONFLICT"


def test_e3_failure_withholds_every_day(monkeypatch):
    monkeypatch.setattr(api, "validate_final", lambda *a: ValidationResult(("tampered",)))
    res = api.optimize(request_data())
    assert res.validation_status == "FAILED"
    assert res.error_code == "FINAL_VALIDATION_FAILED"
    assert not res.days and not res.metrics


@pytest.mark.parametrize("forced,status", [(cp_model.UNKNOWN, "UNKNOWN"), (cp_model.MODEL_INVALID, "MODEL_INVALID"),
                                         (cp_model.INFEASIBLE, "INFEASIBLE")])
def test_raw_unsolved_states_are_preserved(monkeypatch, forced, status):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args: forced)
    monkeypatch.setattr(cp_model.CpSolver, "best_objective_bound", property(lambda s: 0))
    res = api.optimize(request_data())
    assert res.solver_status == status
    assert not res.days
    assert res.phases[0].objective_value is None


def test_secondary_timeout_retains_valid_incumbent(monkeypatch):
    original = cp_model.CpSolver.solve
    calls = []
    def solve(self, model):
        calls.append(self.parameters.max_time_in_seconds)
        status = original(self, model)
        return status if len(calls) == 1 else cp_model.UNKNOWN
    monkeypatch.setattr(cp_model.CpSolver, "solve", solve)
    res = api.optimize(request_data())
    assert res.solver_status == "FEASIBLE"
    assert res.validation_status == "PASSED"
    assert res.days and res.phases[-1].solver_status == "UNKNOWN"
    assert sum(calls) <= 2


def test_feasible_does_not_claim_optimal(monkeypatch):
    original = cp_model.CpSolver.solve
    def solve(self, model):
        original(self, model)
        return cp_model.FEASIBLE
    monkeypatch.setattr(cp_model.CpSolver, "solve", solve)
    res = api.optimize(request_data())
    assert res.solver_status == "FEASIBLE" and res.validation_status == "PASSED"


@pytest.mark.parametrize("change", ["version", "score", "mean", "budget", "search", "duplicate", "extra", "days"])
def test_invalid_input_is_rejected_before_search(change):
    data = request_data()
    if change == "version":
        data["candidates"][0]["dataset_version"] = "other"
    elif change == "score":
        del data["candidates"][0]["personal_scores"]["u1"]
    elif change == "mean":
        data["candidates"][0]["mean_score"] = 0.9
    elif change == "budget":
        data["budget_total"] = -1
    elif change == "search":
        data["search_seconds"] = 30.001
    elif change == "duplicate":
        data["candidates"].append(data["candidates"][0])
    elif change == "extra":
        data["check_availability"] = False
    elif change == "days":
        data["days"] = []
    with pytest.raises(ValidationError):
        api.optimize(data)


def test_secondary_objective_balances_equal_utility():
    data = request_data(2)
    data["days"][0]["end_min"] = 700
    data["candidates"][0]["relevance"] = {"u1": 1, "u2": 0}
    data["candidates"][1].update(group_utility=1000, relevance={"u1": 0.4, "u2": 0.4})
    res = api.optimize(data)
    assert [v.poi_id for v in res.days[0].items] == ["B"]
    assert res.phases[1].objective_value == 4000


def test_tertiary_objective_reduces_travel_with_prior_values_fixed():
    data = request_data(2)
    data["days"][0]["end_min"] = 700
    data["candidates"][1]["group_utility"] = 1000
    for edge in data["edges"]:
        if "A" in (edge["origin"], edge["destination"]):
            edge["duration_minutes"] = 20
    res = api.optimize(data)
    assert [v.poi_id for v in res.days[0].items] == ["A"]
    assert [p.objective_value for p in res.phases] == [1000, 2000, 40]


@pytest.mark.parametrize("size", [1, 10])
def test_supported_party_size_and_per_person_costs(size):
    data = request_data(1)
    template = data["members"][0]
    data["members"] = [{**template, "member_id": f"m{i}"} for i in range(size)]
    p = data["candidates"][0]
    p["personal_scores"] = {m["member_id"]: 0.5 for m in data["members"]}
    p["relevance"] = {m["member_id"]: 0.25 for m in data["members"]}
    res = api.optimize(data)
    assert res.validation_status == "PASSED"
    assert res.metrics["total_cost"] == size * 100 + 20


def test_single_run_thirty_second_cap_is_shared_by_all_objectives():
    data = request_data(1)
    data["search_seconds"] = 30
    res = api.optimize(data)
    assert [p.allocated_seconds for p in res.phases] == [21, 6, 3]
    assert res.metrics["allocated_search_seconds"] <= 30
