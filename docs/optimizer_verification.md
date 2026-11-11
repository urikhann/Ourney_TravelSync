# Optimizer implementation and verification

Date: 2026-11-05 (Asia/Seoul). Branch: `feature/ortools-optimizer`.
Baseline: `a80f48a`. Python 3.13, Windows, OR-Tools 9.15.6755.
No merge or push was performed.

## Results

| Check | Result |
|---|---|
| Root package tests, including 117 optimizer cases | **166 passed**, 21 subtests passed, 19.33 s |
| Existing API suite in a disposable copied workspace | **46 passed**, 18.01 s |
| Existing recommender service suite | **13 passed**, 6.32 s |
| Existing opt-in PostgreSQL suite | **2 skipped**: no disposable test URL configured |
| Tiny independent exhaustive oracle | All **16 cases** matched the full lexicographic optimum; included in root count |
| New HTTP boundary | `/health`, `/docs`, OpenAPI, `/plans`, `/runs`, invalid legacy payloads checked in-process |
| Lockfile consistency | `uv lock --check --offline --cache-dir .uv-cache` passed |
| Syntax / whitespace | `compileall` and `git diff --check` passed |

Total: **225 tests passed**, plus 21 subtests; 2 PostgreSQL tests skipped.
The existing suites emitted dependency deprecation warnings (Starlette/httpx,
SQLite date adapters, importlib metadata); no test failed.

Commands:

```powershell
.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.uv-cache/optimizer-release-pytest
# From services/recommender:
../../.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider
# From repository root:
.venv/Scripts/python.exe -m pytest integration_tests -q -p no:cacheprovider
uv lock --check --offline --cache-dir .uv-cache
.venv/Scripts/python.exe -m compileall -q src/ourney_travelsync/optimizer services/optimizer
git diff --check
```

The API suite's existing conftest deletes `api/test.db`. To avoid changing that
file, the unchanged `api/app`, `api/tests` and `services/mocks` were copied into
`.uv-cache/optimizer-api-6a121ec1`, along with the read-only metadata CSV needed
by the import regression. The copied API suite ran with a new SQLite database
and a temporary pytest directory there. The original `api/test.db` SHA256 before
and after was identical:
`990e3b5b0d3d0d2ef51907255516f8c597e9081c73bd12fecb9b1ba3495b1ae7`.
Existing API behavior, including its legacy mock planner, was regression-tested;
this is not a claim that the production API calls the new optimizer.

## Milestones

| Milestone | Commit | Optimizer tests at that point |
|---|---|---|
| Contracts, optional dependencies and formulation | `4113a1c` | 6 passed |
| Single-day optional circuit and time windows | `827fb21` | 9 passed |
| One to three days and no repeated POIs | `1916b7b` | 13 passed |
| E2 evidence, group budget and hard constraints | `a41e7be` | 34 passed |
| Independent E3 reconstruction and adversarial checks | `3afa798` | 63 passed |
| HTTP service, lexicographic objectives and failure states | `118ac72` | 84 passed |
| Shared-budget expansion, oracle, CI, examples and final regression report | Final validation commit | 117 optimizer cases; full results above |

## Requirement coverage

| SDD scenario / requirement | Evidence |
|---|---|
| T13 waiting after early arrival | Single-day arrival 09:30, opening 10:00, full 60-minute stay |
| T14 final return cannot overrun end | Missing/too-long return rejected; E3 tamper checks |
| T15 total party budget, including all dates/fares | Exact-boundary and one-won-under fixtures, unknown cost/fare exclusion |
| T16 directed/unreachable arcs, subtours, multi-day duplicates | Asymmetric route order, disconnected cycle exclusion, distinct endpoints, cross-day uniqueness |
| T17 INFEASIBLE / UNKNOWN / FEASIBLE / MODEL_INVALID | Raw solver state tests, zero/tiny budget, secondary timeout preserving incumbent |
| T29 operating/cost/dwell unknown after enrichment | E2 excludes only scheduling eligibility with explicit reasons; no zero/all-day substitution |
| E3 / FR-ITIN-OPT-01 | Recomputes visits, times, openings, return, fees, budget, walking, exclusions, access, events, member availability and locks |
| FR-ITIN-OUT-01, Module H output boundary | Dated arrival/start/departure/stay/travel/party cost, return leg, raw statuses/objectives, replay snapshot, per-member metrics |

The exhaustive oracle enumerates every nonempty route per day, schedules earliest
possible visits into split windows, combines disjoint daily routes, and compares
utility / minimum relevance / negative travel lexicographically. It does not use
the CP-SAT model or E2 output. Its random seeds, cases and routing facts are
synthetic and fixed in `tests/test_optimizer_oracle.py`.

## Unfavorable smoke result and performance limit

A single **synthetic**, dense 30-candidate / three-day run with a 3-second cap
returned FEASIBLE / E3 PASSED with **3 visits**, group utility **2,970** and primary
best bound **26,640**. Primary search used 2.107 s (2.1 s configured); the secondary
phase returned UNKNOWN after 0.426 s (0.6 s configured). The incumbent was retained
and no tertiary search ran. End-to-end Python call time was 2.902 s. This run
overlapped the regression suite and is not a controlled performance benchmark.
The large remaining objective gap is retained as a limitation, not a quality win.

Configured search caps sum to ≤30 seconds across phases and optional expansion.
CP-SAT stopping is cooperative: the smoke result demonstrates small timing
overhead beyond a phase's configured cap. This implementation does not promise a
hard 30-second HTTP process deadline. Production performance and the prescribed
20-run fixed-environment p95 evaluation are untested.

## Changed files

| Files | Purpose |
|---|---|
| `src/ourney_travelsync/optimizer/{__init__,contracts,eligibility,solver,validation,api}.py` | Strict snapshots, E2, circuit/time/budget model, staged search, E3 gate and budgeted run coordinator |
| `services/optimizer/{main.py,requirements.txt,Dockerfile}` | Internal standalone HTTP service and container definition |
| `contracts/README.md`, `optimizer.synthetic.request.json`, `optimizer.synthetic.response.json` | New versioned boundary and explicitly synthetic examples; legacy example files retained |
| `pyproject.toml`, `uv.lock` | Optional OR-Tools/Pydantic extra; OR-Tools requires the locked protobuf 6.x dependency |
| `tests/optimizer_fixtures.py`, `tests/test_optimizer_{contracts,solver,eligibility,validation,api,service,expansion,oracle}.py` | Isolated synthetic fixtures, unit, contract, integration, oracle and regression tests |
| `.github/workflows/optimizer.yml` | CI installs the optional backend and runs the root suite |
| `docs/optimizer.md`, `docs/optimizer_verification.md` | Formulation, usage, integration requirements, SDD boundaries and evidence |

No database schema, production app route, recommender calculation, existing
production dataset or unrelated module was changed. Pre-existing untracked
experiments and the enriched demo-candidate CSV were left out of every commit.

## Deviations and untested assumptions before merge

The solver/E2/E3 implementation follows the constraints and objective sequence
in SDD 10–11. Integration adaptations and remaining requirements are explicit:

- The new strict contract uses endpoint IDs resolved upstream rather than
  embedding coordinates. It returns one optimized itinerary and uses synchronous
  internal calls. It is not compatible with the legacy three-themed-plan UI.
- The coordinator accepts pre-enriched initial/expanded snapshots. It budgets at
  most one expansion, but fetching L=200/K=60 recommendations, lazy enrichment,
  routing provider calls, fresh departure-time route checks and their external
  timeouts still need production adapters. No data is fabricated for them.
- The matrix is frozen per day. Conservative estimates/time buckets must come
  from routing adapters; real traffic and operating facts were not validated.
  Evidence timestamps/source claims are trusted inputs, not independently
  corroborated by this module. Cache freshness policy remains upstream.
- The authenticated backend must capture versions, obtain common availability
  and all required facts, set its current 10-second transport timeout above the
  run duration, save the request/result, enforce access control and publish only
  E3 PASSED results. Current app generation still calls the legacy mock.
- No live authenticated optimized trip, PostgreSQL persistence, Docker build or
  container startup was verified. Docker is unavailable here; PostgreSQL checks
  deliberately require an explicitly configured disposable database.
- Supporting up to 10,000 source POIs is a recommendation/enrichment system
  acceptance target. Module H receives at most 60 candidates; no full-system
  scale, real-group satisfaction or production routing quality claim is made.

Detailed wiring requirements and commands: [optimizer.md](optimizer.md).
