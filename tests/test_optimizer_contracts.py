import pytest
pytest.importorskip("pydantic")
from pydantic import ValidationError

from ourney_travelsync.optimizer.contracts import Edge, Fact, Window, ceil_minutes


def test_time_and_unknown_contracts():
    assert ceil_minutes(0.1) == 1
    assert Fact[int]().value is None
    assert not Fact[int](value=0).known
    assert not Edge(date="2026-11-05", origin="a", destination="b", status="KNOWN").usable
    for value in (-1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            ceil_minutes(value)


@pytest.mark.parametrize("start,end", [(600, 600), (700, 600), (-1, 10), (0, 1441), (1.2, 20)])
def test_invalid_windows(start, end):
    with pytest.raises(ValidationError):
        Window(start_min=start, end_min=end)


def test_documented_synthetic_examples_validate_independently():
    from pathlib import Path
    from ourney_travelsync.optimizer.contracts import PlanRequest, PlanResponse
    from ourney_travelsync.optimizer.validation import validate_final
    root = Path(__file__).resolve().parents[1] / "contracts"
    req = PlanRequest.model_validate_json((root / "optimizer.synthetic.request.json").read_text())
    res = PlanResponse.model_validate_json((root / "optimizer.synthetic.response.json").read_text())
    assert req == res.input_snapshot
    assert validate_final(req, res.days).passed
