import pytest
pytest.importorskip("ortools")
from pydantic import ValidationError

from optimizer_fixtures import request_data
from ourney_travelsync.optimizer.api import optimize, optimize_run


def test_one_expansion_recovers_missing_candidates_and_keeps_both_snapshots():
    initial, expanded = request_data(1, 2), request_data(2, 2)
    res = optimize_run(dict(initial=initial, expanded=expanded))
    assert res.validation_status == "PASSED"
    assert len(res.attempts) == 2
    assert res.attempts[0].solver_status == "INFEASIBLE"
    assert len(res.attempts[0].input_snapshot.candidates) == 1
    assert len(res.attempts[1].input_snapshot.candidates) == 2
    assert res.metrics["run_allocated_search_seconds"] <= initial["search_seconds"]


@pytest.mark.parametrize("field,value", [("budget_total", 999999), ("dataset_version", "other"),
                                         ("search_seconds", 30), ("run_id", "other")])
def test_expansion_cannot_relax_or_switch_run(field, value):
    initial, expanded = request_data(1, 2), request_data(2, 2)
    expanded[field] = value
    with pytest.raises(ValidationError):
        optimize_run(dict(initial=initial, expanded=expanded))


def test_valid_incumbent_prevents_unnecessary_expansion():
    res = optimize_run(dict(initial=request_data(1), expanded=request_data(2)))
    assert len(res.attempts) == 1
    assert res.validation_status == "PASSED"


def test_zero_or_tiny_budget_never_claims_feasible_without_a_solution():
    for budget in (0, 1e-12):
        data = request_data()
        data["search_seconds"] = budget
        res = optimize(data)
        assert res.solver_status == "UNKNOWN"
        assert not res.days


def test_expansion_does_not_reset_zero_budget():
    initial, expanded = request_data(1, 2), request_data(2, 2)
    initial["search_seconds"] = expanded["search_seconds"] = 0
    res = optimize_run(dict(initial=initial, expanded=expanded))
    assert res.solver_status == "UNKNOWN"
    assert len(res.attempts) == 1
    assert res.metrics["run_allocated_search_seconds"] == 0
