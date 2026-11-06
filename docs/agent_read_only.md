# TravelSync read-only agent tools

This milestone adds an authenticated inspection endpoint and three typed
read tools. Existing TF-IDF scoring, recommendation behavior, APIs and
database schemas are preserved.

It does not invoke an LLM or modify preferences, recommendations or plans.

## Configuration

`FEATURE_AGENT` defaults to `false`.

For a local Python backend, set the variable in the same PowerShell
session used to start the backend:

```powershell
$env:FEATURE_AGENT = "true"
```

Restart the backend using its existing startup command and database
configuration.

To disable the endpoint:

```powershell
$env:FEATURE_AGENT = "false"
```

Restart the backend after changing the environment.

Direct Python startup reads process environment variables. It does not
automatically load the repository's root `.env`.

The existing Docker Compose API service reads the root `.env` through
`env_file`. For that deployment, configure `FEATURE_AGENT` there and
recreate the API container through the normal deployment workflow.
Docker operation was not verified in this milestone.

No LLM API key is required for these read tools.

## Endpoint

```text
GET /trips/{trip_id}/agent/context
Authorization: Bearer <existing-access-token>
```

Optional query parameters:

- `run_id`: positive integer identifying a saved recommendation Run.
- `poi_ids`: repeated POI IDs, maximum 20, without duplicates or blanks.

Example request shape:

```text
/trips/{trip_id}/agent/context?run_id=12&poi_ids=kakao:123&poi_ids=tour:456
```

The IDs above are examples. Use actual IDs from an authorized trip and
its existing recommendation results.

Without POI IDs, the endpoint returns trip metadata and a Run summary.
With POI IDs, it also returns POI information and, when a Run exists,
saved recommendation evidence.

An omitted Run ID selects the trip's latest Run. When no Run exists,
POI information uses the active dataset. A missing POI in a pinned dataset
is reported as missing rather than fetched from a newer dataset.

## Tools and evidence

- `get_trip_context`: trip dates, basic metadata, member count and saved
  Run metadata.
- `get_poi_information`: bounded POI information with evidence labels.
- `get_recommendation_evidence`: saved group ranks, mean scores, selection
  gains, utility values and sanitized exclusion codes.

Other members' raw preferences, private exclusion details, per-member
scores, invitation codes and full Run requests are omitted.

Evidence labels:

| Label | Meaning |
| --- | --- |
| UNKNOWN | No value is stored. |
| REPORTED | The application marks the value as known; the tool supplies no independent verification. |
| ESTIMATED | The application explicitly marks the value as estimated. |
| UNVERIFIED | A value exists without sufficient supporting status. |

Indoor flags remain unverified. Imported flags may be inferred from
Features. A stored zero cost remains distinct from missing cost.

Record source and record-update time describe the database record.
They do not establish field-specific verification. These tools do not
populate verification timestamps.

Recommendation values are copied from the saved response without
recalculation or rescaling. Candidate rank, selection gain and itinerary
utility retain their separate meanings. Missing values remain missing.

The tool does not expose causal TF-IDF attribution or claim that a
recommendation is operationally feasible.

## Errors

| Status | Meaning |
| --- | --- |
| 401 | Missing, invalid, revoked or unverified-user authentication |
| 404 | Feature disabled, inaccessible trip, or inaccessible/missing Run |
| 409 | Detected Run mismatch while assembling context; retry the read |
| 422 | Invalid path or query parameters |
| 502 | Malformed saved recommendation evidence |
| 503 | No active dataset when a POI lookup needs one |

The actor comes from authentication, not request parameters.
Each tool independently checks trip membership and scopes Run access.

## Verification

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/check_agent_regressions.py
```

The script runs the full API suite in a disposable copy, then runs the
real recommender and package suites in separate processes. It retains
logs in a temporary directory and disables PostgreSQL integration tests
unless the script is deliberately changed for a disposable database.

Recorded on 7 November 2026:

| Suite | Result |
| --- | --- |
| Full API | 100 passed, including 54 new tool/endpoint cases |
| Real recommender service | 13 passed |
| Package | 49 passed, 2 skipped, 21 subtests passed |

The skipped tests require disposable PostgreSQL configuration.
Existing deprecation warnings remain.

Coverage includes authentication, membership revocation, cross-trip Run
IDs, dataset pinning, privacy filtering, uncertainty labels, malformed
evidence, query validation, no-write behavior, absence of computation
service calls, full-application registration and access to the existing
trip endpoint while the feature is disabled.

## Current limits

This is a backend inspection capability, not a conversational agent.

Saved itinerary items and plan comparisons are not yet included in trip
context. There is no LLM invocation, chat interface, job lifecycle,
proposal approval or itinerary application.

The context-change guard detects a Run mismatch. It is not transactional
approval or concurrency protection for future itinerary changes.

The application still uses stand-ins for planning and the existing agent
service. This milestone does not establish live routing, itinerary
feasibility, deployed browser operation or PostgreSQL integration.
