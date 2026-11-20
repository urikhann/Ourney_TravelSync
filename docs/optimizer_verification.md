# Testing the optimizer

Tests use synthetic operating facts and routes, isolated temporary databases,
small-instance exhaustive comparisons, and deliberate mutations of solver
outputs. They check software contracts and scheduling behavior; they do not
establish live provider accuracy or recommendation satisfaction.

## Install and run

From the repository root:

```powershell
uv sync --locked --extra optimizer --extra ingestion --extra postgres --extra viewer
uv pip install -r api/requirements.txt -r services/recommender/requirements.txt -r services/planning_data/requirements.txt
uv pip check
uv run --no-sync pytest tests -q
uv run --no-sync pytest api/tests -q
uv run --no-sync pytest services/recommender/tests -q
uv lock --check
uv build
```

The minimal package suite also runs with `uv sync --locked` and
`uv run --locked pytest -q`. Tests for uninstalled optional components skip
before importing them. CI runs minimal, ingestion/viewer, and optimizer-enabled
environments so optional dependencies are tested separately.

## Coverage

| Area | Checks |
|---|---|
| Contracts | Typed facts, version consistency, invalid payloads and unknown values |
| Eligibility | Complete stay windows, closures, festivals, exclusions and enabled constraints |
| Solver | Directed routes, return legs, availability, budgets, locks, daily composition and search limits |
| Objectives | Small-instance exhaustive oracle, retained incumbents and phase statuses |
| E3 | Independent replay of visit times, edges, costs, meal assignments and member rules |
| API | Authentication, snapshot matching, stale inputs, private audits and saved-plan/vote preservation |
| Providers | Mocked route failures, quota guards, cache keys, split schedules and effective dates |

## Optional integration checks

Set `TRAVELSYNC_TEST_DATABASE_URL` to a disposable PostgreSQL database, then run
`uv run --no-sync pytest integration_tests -q`. Tests create and drop their own
isolated schemas. Do not use a shared database.

Set `TRAVELSYNC_LIVE_OPTIMIZER_TEST=1` for
`api/tests/test_optimizer_live.py`. This starts local API, recommender, optimizer,
and synthetic-adapter processes. It does not call a paid routing provider.

Solver time limits cover search phases, not parsing, model construction,
transport, or validation. CP-SAT stopping is cooperative. Inspect phase bounds,
statuses, and timings rather than treating a timeout incumbent as optimal.
