"""Settings from environment variables (see ../.env.example)."""
import os


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


SECRET_KEY = _env("SECRET_KEY", "dev-only-secret-change-me-before-deploying-0000")
DATABASE_URL = _env("DATABASE_URL", "sqlite:///./travelsync.db")
ACCESS_TOKEN_MINUTES = int(_env("ACCESS_TOKEN_MINUTES", "1440"))

CODE_TTL_MINUTES = 10
CODE_MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60

SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
MAIL_FROM = _env("MAIL_FROM", "TravelSync <no-reply@travelsync.app>")

WEB_ORIGIN = _env("WEB_ORIGIN", "http://localhost:5173")
WEB_URL = _env("WEB_URL", WEB_ORIGIN)  # used in invite links

# Service URLs; local recommendation/availability defaults use services/mocks.
RECOMMENDER_URL = _env("RECOMMENDER_URL", "http://localhost:8001")
OPTIMIZER_URL = _env("OPTIMIZER_URL", "http://localhost:8003")
PLANNING_DATA_URL = _env("PLANNING_DATA_URL")  # trusted enrichment/routing adapter; no mock fallback
PLANNING_DATA_TOKEN = _env("PLANNING_DATA_TOKEN")  # header only; never stored in snapshots
PLANNING_DATA_TIMEOUT_SECONDS = float(_env("PLANNING_DATA_TIMEOUT_SECONDS", "20"))
OPTIMIZER_TIMEOUT_SECONDS = float(_env("OPTIMIZER_TIMEOUT_SECONDS", "45"))
OPTIMIZER_SEARCH_SECONDS = 30
AVAILABILITY_URL = _env("AVAILABILITY_URL", "http://localhost:8001")
AGENT_URL = _env("AGENT_URL", "http://localhost:8001")
SERVICE_TIMEOUT_SECONDS = float(_env("SERVICE_TIMEOUT_SECONDS", "10"))
# The AI agent calls an LLM several times per answer, so it gets more time than the other services.
AGENT_TIMEOUT_SECONDS = float(_env("AGENT_TIMEOUT_SECONDS", "210"))

# Both services resolve the same hash-validated snapshot specification.
from ourney_travelsync.deployment import SNAPSHOT, snapshot
_DATASET = snapshot()
_DEFAULT_CSV = str(SNAPSHOT.parent / _DATASET["csv"])
PLACES_CSV = _env("PLACES_CSV", _DEFAULT_CSV)  # explicit empty = isolated sample mode
PLACES_VERSION = _env("PLACES_VERSION", _DATASET["dataset_version"])
HOURS_CSV = _env("HOURS_CSV", str(SNAPSHOT.parent / _DATASET["operating_csv"]))
# Compatibility setting only; startup never imports or activates deployment data.
PLACES_FOLLOW_ENV = _env("PLACES_FOLLOW_ENV", "false").lower() in ("1", "true", "yes")

# Group recommendation settings: L = each member's top list, K = group candidates.
REC_TOP_L = int(_env("REC_TOP_L", "100"))
REC_GROUP_K = int(_env("REC_GROUP_K", "30"))

# Supported limits: groups of 1–10 people, trips of 1–3 days.
MAX_GROUP_MEMBERS = int(_env("MAX_GROUP_MEMBERS", "10"))
MAX_TRIP_DAYS = int(_env("MAX_TRIP_DAYS", "3"))

# Invite links stop working after this many days (the owner can make a new link any time).
INVITE_TTL_DAYS = int(_env("INVITE_TTL_DAYS", "7"))

# Kakao Local keyword search for start/end locations on the Plan screen (optional; same key as planning-data).
KAKAO_REST_API_KEY = _env("KAKAO_REST_API_KEY")

# Controls availability-check endpoints and holiday display. Optimizer hours policy is separate.
FEATURE_AVAILABILITY = _env("FEATURE_AVAILABILITY", "false").lower() in ("1", "true", "yes")

# Bounded chat replacement search, separate from full-plan generation.
REPLACEMENT_CANDIDATE_LIMIT = min(20, max(1, int(_env("REPLACEMENT_CANDIDATE_LIMIT", "8"))))
REPLACEMENT_REPAIR_RADIUS = min(3, max(0, int(_env("REPLACEMENT_REPAIR_RADIUS", "1"))))
REPLACEMENT_SHIFT_MINUTES = min(120, max(0, int(_env("REPLACEMENT_SHIFT_MINUTES", "30"))))
REPLACEMENT_SOLVER_SECONDS = min(30.0, max(0.0, float(_env("REPLACEMENT_SOLVER_SECONDS", "2"))))
REPLACEMENT_TOTAL_SECONDS = max(1.0, float(_env("REPLACEMENT_TOTAL_SECONDS", "20")))
REPLACEMENT_VALID_SECONDS = max(1.0, float(_env("REPLACEMENT_VALID_SECONDS", "600")))
REPLACEMENT_DISTANCE_WEIGHT = max(0.0, float(_env("REPLACEMENT_DISTANCE_WEIGHT", "0.15")))
REPLACEMENT_FAIRNESS_WEIGHT = max(0.0, float(_env("REPLACEMENT_FAIRNESS_WEIGHT", "0.7")))
REPLACEMENT_RELEVANCE_WEIGHT = max(0.0, float(_env("REPLACEMENT_RELEVANCE_WEIGHT", "0.3")))
PENDING_ACTION_VALID_SECONDS = max(1.0, float(_env("PENDING_ACTION_VALID_SECONDS", "300")))
PLANNING_ACTION_SECONDS = max(1.0, float(_env("PLANNING_ACTION_SECONDS", "60")))
