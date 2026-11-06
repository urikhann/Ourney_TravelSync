# Production app → Module H integration

Implemented 2026-11-06 on `feature/ortools-optimizer`. This supersedes the
standalone-only wiring status in the 2026-11-05 verification report.

The production API's `POST /trips/{id}/itineraries/generate` now calls the real
optimizer. It never calls the greedy mock. Docker Compose starts Module H on
port 8003 and points the API to `http://optimizer:8003`; local API defaults use
`http://localhost:8003`. Only E3-passed itineraries are published. Legacy mock
plans have no publication audit and are hidden from listing and voting.

## Configuring the planning-data adapter

Set these **server-side** variables (template: `.env.example`):

```dotenv
PLANNING_DATA_URL=https://YOUR-TRUSTED-PLANNING-SERVICE
PLANNING_DATA_TOKEN=
PLANNING_DATA_TIMEOUT_SECONDS=20
OPTIMIZER_TIMEOUT_SECONDS=45
```

Use your actual trusted base URL. The API calls `POST {PLANNING_DATA_URL}/snapshot`.
If supplied, the token is sent in an Authorization Bearer header; it is never
included in stored planning or optimizer snapshots. Do not commit real tokens.
Compose passes these values to the API. Local uvicorn processes read exported
environment variables; `api/app/config.py` does not automatically load `.env`.
Update any old local `OPTIMIZER_URL=http://localhost:8001` setting to port 8003.

With no adapter configured, generation returns HTTP 503 /
`planning_data_not_configured`, records the failure and leaves any saved plan
and votes untouched. The web app explains the missing configuration and keeps
recommendations available. There is no synthetic, straight-line, zero-cost or
always-open production fallback. The repository does not include a real routing
or enrichment provider implementation, and none was configured in this session.

Request shape (`planning-data-1.0`):

| Field | Meaning |
|---|---|
| `context_id` | SHA256 of the complete server-built context; echo exactly |
| `run_id` | Recommendation run ID plus optimization audit ID |
| `dataset_version`, `transport_mode`, `group_size` | Pinned version, selected mode and actual member count |
| `days` | All requested dates, fixed integer departure/end minutes and endpoint IDs |
| `points` | Endpoint ID → explicit label, latitude, longitude |
| `pois` | Only captured GFAR candidates: ID, coordinates, raw metadata and field-status hints |
| `required_access` | Group's explicitly requested accessibility properties |

Metadata hints are not verified scheduling facts. The adapter must resolve
operating hours, closures, holidays, breaks, overnight intervals, events, fees,
dwell evidence and required access using its actual sources and freshness policy.
It must obtain directed routes for the requested transport/reference times,
including endpoint legs, not use place-search results as travel durations.

Response is the strict `app.optimization.PlanningSnapshot` model:

```json
{
  "schema_version": "planning-data-1.0",
  "context_id": "EXACT-REQUEST-CONTEXT-ID",
  "dataset_version": "EXACT-REQUEST-DATASET",
  "transport_mode": "EXACT-REQUEST-MODE",
  "operating_snapshot_id": "YOUR-PINNED-OPERATING-SNAPSHOT",
  "routing_version": "YOUR-PINNED-ROUTING-SNAPSHOT",
  "pois": [],
  "edges": []
}
```

This example intentionally has no scheduling data; it cannot produce an
itinerary. Each `pois` row may supply `poi_id`, `duration_min`,
`cost_per_person`, dated `operations` and `access`. Facts use
`{value,status,source,verified_at,rule_version,user_upper_bound}` (see the typed
models). Missing rows/fields stay UNKNOWN. ESTIMATED dwell requires a rule
version; estimated cost requires an explicit user upper bound. A known dated
closure flag is required separately from opening intervals; festivals need
known dated event validity. Never infer holiday availability from a missing row.

Each edge supplies `date`, `origin`, `destination`, `status`,
`duration_minutes`, `distance_m`, `group_fare`, `walk_minutes`, `provider` and
`fetched_at`. Origins/destinations must be requested POI or endpoint IDs. Safe
integer rounding belongs upstream. Unknown fares or walking components remain
null; unsupported transport modes must fail or return unknown routes. Monetary
coverage is party-size × supplied POI fees plus every selected group route fare.
Any required additional spending must be represented explicitly in those inputs.

The adapter cannot change recommendation scores/ranks, member rules, group size,
dates, locations or budget. Context/dataset/mode mismatches, unexpected/duplicate
POIs and unknown route nodes are rejected. Credentials and upstream HTTP error
bodies are not echoed in public errors.

## Authenticated generation and publication

The trip owner sends `GenerateInput`: all trip dates, per-day start/end minutes
and explicit endpoint coordinates, group budget and mode. Leg/walking limits,
required access and locked visits are optional hard constraints. The browser can
select a recommended location or enter another location's coordinates. It has
no fields for submitting candidate scores, operating facts or route durations.

All members must have confirmed each trip date. The current availability model
records dates, not personal hourly windows: an explicit free-date confirmation
is interpreted within the submitted group daily window. The trip-room UI states
that meaning. Granular member-hour entry is still outside this app's model.
Do not treat an absent availability row as consent for the whole trip.

The API pins the latest GFAR recommendation, dataset/mapping/schema, captured
member preference versions, exclusions and confirmed dates. Scores are converted
from the declared 1–5 scale to 0–1 without clamping invalid data. Mean is computed
from the captured member scores to avoid the recommender's display rounding.
Each candidate carries the actual GFAR Borda relevance exported by its producing
recommender; missing relevance requires recommendation regeneration, not a
fabricated zero vector. This adds metadata without changing GFAR selection.

After the remote solver returns, the API requires an exact matching snapshot,
OPTIMAL/FEASIBLE status, PASSED validation, no domain error and a fresh local E3
reconstruction from the original request. It checks for changed preferences,
membership, exclusions, availability, recommendations and a newer generation
request before replacing old plans. On success, one optimized itinerary replaces
the old itinerary/votes atomically. Failures keep the previous plan and votes.
The public view contains group costs, server travel/return times, validation and
solver status, source-version IDs and exclusion reasons; it excludes private
input snapshots and per-member score/relevance matrices.

Generation remains synchronous. Separate 45-second optimizer and 20-second data
transport timeouts cover the optimizer's shared ≤30-second search cap plus
overhead. External data latency is outside the solver search budget. This app
submits the captured candidate pool once; automatic L/K expansion and live
departure-time route refresh remain upstream orchestration work.

## Necessary additive schema change

Added only the API database table `itinerary_optimization_runs`. It stores the
requesting user, trip/recommendation IDs, explicit configuration, captured input
state, planning-data request/response, optimizer request/response, terminal status
and optional published itinerary ID. Failed attempts need this separate audit;
using an empty itinerary as a failed-run record would misrepresent publication.

The API's existing `Base.metadata.create_all()` creates this new table on startup.
Existing tables/columns and canonical CLI database schemas are unchanged; no data
migration or production-database command was executed here. Deploy with the
normal API schema-creation permission, or have the database owner create this
table from `app.models.OptimizationRun.__table__` before startup. Snapshots are
private DB records; there is no public endpoint returning the raw audit.

## Validation and limits

- Root suite: 166 passed plus 21 subtests.
- API suite: **63 passed**, including the opt-in real HTTP test, in 19.93 seconds.
  Includes real CP-SAT and local E3, adapter failure, missing cost,
  context mismatch, forged PASSED response, auth, stale members/preferences,
  private persistence, token isolation and legacy-plan rejection.
- Opt-in loopback test starts four actual uvicorn processes (API, real
  recommender, optimizer, synthetic adapter). It signs up/verifies/logs in a
  fixture user, generates recommendations and a PASSED itinerary, votes, then
  verifies unknown-cost failure preserves the saved plan/vote.
- Web TypeScript and production Vite build passed with Node 20.13.1. The shipped
  Compose web service continues to use Node 22.
- Recommender service suite: **13 passed**. Total across root/API/recommender:
  **242 passed**, plus 21 subtests. Two opt-in PostgreSQL tests were skipped.
- PostgreSQL and Docker deployment were not exercised; no disposable PostgreSQL
  URL or Docker executable is available. All executed database tests use newly
  created SQLite databases. The workspace's existing `api/test.db` is unchanged.
- The planning-data service used in tests is explicitly synthetic and has no
  production fallback path. Real-provider correctness/freshness, traffic changes,
  full-system scale and satisfaction remain unverified.

Run the API suite from `api/` (install `.[optimizer]` and `api/requirements.txt`):

```powershell
$env:TRAVELSYNC_LIVE_OPTIMIZER_TEST='1'
../.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --basetemp=../.uv-cache/optimizer-app-verification
```

Without the opt-in variable, the loopback process test is skipped. API conftest
always uses a new temporary SQLite database and no longer deletes `api/test.db`.
Synthetic runtime fixtures live only under `api/tests/`.

Changed-file scope: API generation/adapters/configuration/models and tests;
recommender relevance metadata (selection behavior preserved); Compose/API image
and CI dependencies; web planning form, itinerary display/types/errors; configuration
templates and integration documentation. Source datasets, existing databases,
canonical CLI schemas and unrelated experiments were not changed or committed.
