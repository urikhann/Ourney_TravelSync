# API and optimizer integration

The production API's `POST /trips/{id}/itineraries/generate` calls the
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

Use your actual trusted base URL. The API uses `POST /selected-snapshot` for driving.
The existing `POST /snapshot` contract is still available to direct callers.
If supplied, the token is sent in an Authorization Bearer header; it is never
included in stored planning or optimizer snapshots. Do not commit real tokens.
Compose passes these values to the API. Local uvicorn processes read exported
environment variables; `api/app/config.py` does not automatically load `.env`.
Update any old local `OPTIMIZER_URL=http://localhost:8001` setting to port 8003.

With no adapter configured, generation returns HTTP 503 /
`planning_data_not_configured`, records the failure and leaves any saved plan
and votes untouched. The web app explains the missing configuration and keeps
recommendations available. Provisional estimates never qualify as verified
routing evidence. Compose includes the Kakao driving adapter at `http://planning-data:8004`.
See [adapter configuration](../services/planning_data/README.md).

Request shape (`planning-data-1.0`):

| Field | Meaning |
|---|---|
| `context_id` | SHA256 of the complete server-built context; echo exactly |
| `run_id` | Recommendation run ID plus optimization audit ID |
| `dataset_version`, `transport_mode`, `group_size` | Pinned version, selected mode and actual member count |
| `days` | All requested dates, fixed integer departure/end minutes and endpoint IDs |
| `points` | Endpoint ID → explicit label, latitude, longitude |
| `pois` | Day-specific catalogue candidates: ID, coordinates, raw metadata and field-status hints |
| `required_access` | Group's explicitly requested accessibility properties |
| `route_legs` | Selected-snapshot only. Dated, directed legs including start and return. An empty list loads operating facts without provider calls. |
| `routing_attempts`, `expand_routing_budget` | Selected-snapshot only. Cumulative per-endpoint attempt counts and permission to use the hard plan cap for a repair round. |

Driving generation first loads operating facts and solves a provisional plan.
It uses Haversine distance as the third objective after group utility and
least-served-member relevance. Draft travel minutes use a 1.3 road factor and
25 km/h speed. The optimizer returns drafts with validation status `NOT_RUN`.
The adapter returns only the requested legs and cumulative routing attempts.
The solver then reschedules against verified legs, keeping existing planning
conditions. If a verified plan cannot be built, up to two repair rounds reuse
verified legs, avoid failed edges and query only new legs. All six possible solver
passes share the configured search allowance. The existing endpoint daily quotas
and combined plan hard cap apply across rounds. Final validation uses only the
provider route graph. See [routing quota settings](routing_quota_guard.txt).

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
and explicit endpoint coordinates, duration overrides, locks, and transport mode. Leg/walking limits,
required access and locked visits are optional hard constraints. The browser can
select a recommended location or enter another location's coordinates. It has
no fields for submitting candidate scores, operating facts or route durations.

All members must have confirmed each trip date. The current availability model
records dates, not personal hourly windows: an explicit free-date confirmation
is interpreted within the submitted group daily window. The trip-room UI states
that meaning. Granular member-hour entry is still outside this app's model.
Do not treat an absent availability row as consent for the whole trip.

The API pins the latest GFAR recommendation, dataset/mapping/schema, captured
member preference versions, exclusions and confirmed dates. Scores on the declared
1–5 scale are converted to 0–1 without clamping invalid data. E5's
`relative_relevance_0_1` and legacy `0-1` scores retain their captured values.
Mean is computed from the captured member scores to avoid the recommender's
display rounding. E5 remains the default individual scorer, including its explicit
feature fallback; its ranking relevance is not displayed as group-fit percentages.
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

The API database table `itinerary_optimization_runs`. It stores the
requesting user, trip/recommendation IDs, explicit configuration, captured input
state, planning-data request/response, optimizer request/response, terminal status
and optional published itinerary ID. Failed attempts need this separate audit;
using an empty itinerary as a failed-run record would misrepresent publication.

The API's existing `Base.metadata.create_all()` creates this new table on startup.
The standalone recommendation package uses a separate database schema. Deploy with the
normal API schema-creation permission, or have the database owner create this
table from `app.models.OptimizationRun.__table__` before startup. Snapshots are
private DB records; there is no public endpoint returning the raw audit.

## Planning policy and validation

App requests enforce known opening intervals and closures, allow unknown hours
with warnings, and disable budget and travel-minimization objectives. Duration
estimates and overrides are resolved consistently for retrieval and scheduling.
The UI reports unknown hours, estimated stays, and the known-cost subtotal.
See [planning rules](agreed_planning_implementation_20261117.md).

The API converts only declared 1–5 scores with `(value - 1) / 4`. Relative
relevance remains on its captured scale. GFAR relevance and rank-derived group
utility remain separate; neither is measured satisfaction.

Tests cover real CP-SAT with synthetic operating/routes, forged solver results,
stale inputs, authorization, private persistence, and saved-plan/vote preservation.
The optional loopback test starts the API, recommender, optimizer, and synthetic
adapter as separate local processes. See [test setup](optimizer_verification.md).
