import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
_TEST_DIR = tempfile.TemporaryDirectory(prefix="travelsync-api-")
DB = Path(_TEST_DIR.name) / "test.db"  # never delete or migrate an existing workspace database
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ["PLACES_CSV"] = ""  # tests use the 28 sample places
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "services" / "mocks"))

import httpx  # noqa: E402

from app import config, services  # noqa: E402
from app.main import app  # noqa: E402,F401  (creates tables + seeds places)

# Load the mock services in-process and route the backend's service calls to them.
spec = importlib.util.spec_from_file_location("mocks_main", ROOT.parent / "services" / "mocks" / "main.py")
mocks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mocks)
for url in {config.RECOMMENDER_URL, config.AVAILABILITY_URL, config.AGENT_URL}:
    services.TEST_TRANSPORTS[url] = httpx.ASGITransport(app=mocks.app)

spec = importlib.util.spec_from_file_location("optimizer_main", ROOT.parent / "services" / "optimizer" / "main.py")
optimizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(optimizer)
services.TEST_TRANSPORTS[config.OPTIMIZER_URL] = httpx.ASGITransport(app=optimizer.app)


def pytest_sessionfinish(session, exitstatus):
    from app.db import engine
    engine.dispose()
    mocks.engine.dispose()
    _TEST_DIR.cleanup()


@pytest.fixture
def planning_adapter(monkeypatch):
    """Synthetic evidence ONLY for isolated API tests; no production fallback."""
    from fastapi import FastAPI
    upstream = FastAPI()
    control = SimpleNamespace(calls=[], transform=lambda req, response: response)
    stamp = "2026-11-01T00:00:00Z"
    def fact(value):
        return dict(value=value, status="KNOWN", source="synthetic integration fixture", verified_at=stamp)
    @upstream.post("/snapshot")
    def snapshot(req: dict):
        control.calls.append(req)
        ids = [p["poi_id"] for p in req["pois"]]
        response = dict(schema_version="planning-data-1.0", context_id=req["context_id"],
                        dataset_version=req["dataset_version"], transport_mode=req["transport_mode"],
                        operating_snapshot_id="synthetic-ops", routing_version="synthetic-routes",
                        pois=[dict(poi_id=pid, duration_min=fact(30), cost_per_person=fact(100),
                                   operations={d["date"]: dict(openings=fact([dict(start_min=480, end_min=1320)]),
                                                              closed=fact(False), event_active=fact(True)) for d in req["days"]})
                              for pid in ids],
                        edges=[dict(date=d["date"], origin=a, destination=b, status="KNOWN",
                                    duration_minutes=10, group_fare=10, walk_minutes=2, distance_m=500,
                                    provider="synthetic integration fixture", fetched_at=stamp)
                               for d in req["days"] for a in [d["start_point"], *ids]
                               for b in [*ids, d["end_point"]] if a != b])
        return control.transform(req, response)
    monkeypatch.setattr(config, "PLANNING_DATA_URL", "http://planning-test")
    monkeypatch.setattr(config, "OPTIMIZER_SEARCH_SECONDS", 2)
    monkeypatch.setitem(services.TEST_TRANSPORTS, "http://planning-test", httpx.ASGITransport(app=upstream))
    return control
