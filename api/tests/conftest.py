import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "test.db"
if DB.exists():
    DB.unlink()
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
for url in {config.RECOMMENDER_URL, config.OPTIMIZER_URL, config.AVAILABILITY_URL, config.AGENT_URL}:
    services.TEST_TRANSPORTS[url] = httpx.ASGITransport(app=mocks.app)
