import importlib.util
from pathlib import Path

import pytest
pytest.importorskip("ortools")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from optimizer_fixtures import request_data
from ourney_travelsync.optimizer.contracts import PlanRequest


def test_http_boundary_and_legacy_rejection():
    path = Path(__file__).resolve().parents[1] / "services/optimizer/main.py"
    spec = importlib.util.spec_from_file_location("optimizer_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        assert client.get("/health").json()["module"] == "H"
        assert client.get("/docs").status_code == 200
        assert "/runs" in client.get("/openapi.json").json()["paths"]
        res = client.post("/plans", json=request_data(2, 2))
        assert res.status_code == 200
        assert res.json()["validation_status"] == "PASSED"
        req = PlanRequest.model_validate(res.json()["input_snapshot"])
        assert len(req.days) == 2
        legacy = client.post("/plans", json={"trip_id": "legacy", "secret": "not-echoed"})
        assert legacy.status_code == 422
        assert "not-echoed" not in legacy.text
        expanded = client.post("/runs", json={"initial": request_data(1, 2), "expanded": request_data(2, 2)})
        assert expanded.status_code == 200
        assert expanded.json()["validation_status"] == "PASSED"
        assert len(expanded.json()["attempts"]) == 2
