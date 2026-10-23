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

# Teammates' services. Default: the mock service in services/mocks.
RECOMMENDER_URL = _env("RECOMMENDER_URL", "http://localhost:8001")
OPTIMIZER_URL = _env("OPTIMIZER_URL", "http://localhost:8001")
AVAILABILITY_URL = _env("AVAILABILITY_URL", "http://localhost:8001")
AGENT_URL = _env("AGENT_URL", "http://localhost:8001")
SERVICE_TIMEOUT_SECONDS = float(_env("SERVICE_TIMEOUT_SECONDS", "10"))

# Place data: the data pipeline's CSV. Imported automatically into an empty database (see app/seed.py).
# Set PLACES_CSV= (empty) to use the 28 sample places instead.
_DEFAULT_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "places", "travelsync_final_places.csv")
PLACES_CSV = _env("PLACES_CSV", _DEFAULT_CSV)

# Group recommendation settings (SDD 8.3 / 9.2): L = each member's top list, K = group candidates.
REC_TOP_L = int(_env("REC_TOP_L", "100"))
REC_GROUP_K = int(_env("REC_GROUP_K", "30"))

# Limits from the SRS design range: groups of 1–10 people, trips of 1–3 days.
MAX_GROUP_MEMBERS = int(_env("MAX_GROUP_MEMBERS", "10"))
MAX_TRIP_DAYS = int(_env("MAX_TRIP_DAYS", "3"))

# Feature switches. The availability checker (opening hours / holidays) is postponed for the first version:
# plans are built without checking opening hours. Set FEATURE_AVAILABILITY=true to turn it back on.
FEATURE_AVAILABILITY = _env("FEATURE_AVAILABILITY", "false").lower() in ("1", "true", "yes")
