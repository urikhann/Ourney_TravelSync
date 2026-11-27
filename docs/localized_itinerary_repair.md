# Validated chat replacements

Follow-up: [structured confirmation, lock provenance and planning scopes](conversation_state_and_replanning.md).

Implemented on `improvements`, November 26, 2026. No database migration is required.
The implementation uses the existing GFAR recommendation snapshot, planning-data
adapter, E2 checker, E3 validator, private optimization audits and group votes.

## Failure analysis

Confirmed by inspecting the original implementation at
`89b87ff5422bd33e7f18c1ea297952af708c2751`:

- `services/agent/tools.py:TripData.validate` allowed unknown hours and did not
  check route connections or a complete modified itinerary. Its output was a
  suggestion, not evidence of feasibility.
- `api/app/routes/chat.py:ask_agent` made that suggestion pending immediately.
- `api/app/proposals.py:apply` only optimized after approval. Its first attempt
  locked retained stops by date, allowing their times to move across the trip.
  Its second attempt kept only original locks and the replacement, allowing
  unrelated stops and dates to change. Both attempts called full generation.
- The rules fallback recognized rain and walking, but had no replacement intent.
  A failed provider request could therefore produce the unrelated rain help text.
- `optimizer/api.py` maps solver `UNKNOWN` without an incumbent to
  `SEARCH_TIMEOUT`, and `INFEASIBLE` to `NO_FEASIBLE_ITINERARY`. A timeout does
  not establish infeasibility. The old approval path could encounter either.

The historical November 26 itinerary, provider responses and correlated logs were
not supplied. The exact cause of failure for 갤러리지팩 or 타이드 is **unconfirmed**.
Unverified evening hours, travel distance and schedule slack are plausible factors,
not established diagnoses. The regression suite reproduces the Korean request,
November 30 date and 19:08 target using explicitly synthetic operational facts.
It does not invent production opening hours or claim a live historical replay.

## Corrected execution path

```text
Mention / model-originated suggestion
  → deterministic intent and scheduled-occurrence resolution
  → bounded candidates from the pinned GFAR planning snapshot
  → E1 exclusions and explicit supported category constraint
  → lazy planning-data refresh and only the two replacement route legs
  → E2 eligibility
  → fixed-neighbor repair, then bounded local time adjustment
  → if necessary, bounded solve of the affected day only
  → independent E3 over the complete itinerary
  → private VALIDATED draft + group-facing PENDING proposal
  → membership check, majority vote, itinerary/constraint/expiry checks
  → refresh operational facts/routes and revalidate the exact approved schedule
  → atomic new version + APPLIED proposal + approval result message
```

The LLM cannot publish a plan or certify feasibility. Explicit replacement requests
avoid LLM latency entirely. Model suggestions and rule suggestions use the same
backend validation gate. Unknown replacement hours, provisional hours, unknown
routes, locked/started targets and failed E3 checks cannot produce votable drafts.
Retained visits continue to follow the application's existing unknown-hours warning
policy. A validated replacement is not a claim that all retained visits have verified
opening hours. Budget enforcement remains disabled in the app by existing product
policy. The repair engine still rejects over-budget changes when enforcement is enabled.

## Target resolution and candidate ranking

Names are resolved against the pinned dataset's primary and Korean names. Exact
matches take precedence over partial, case-insensitive matches. Date/time qualifiers
select an occurrence. Duplicate names or occurrences require clarification. Relative
references without an unambiguous named anchor also request a date/time rather than
selecting an arbitrary activity. The removed POI and all existing visits are excluded.

The bounded pool reuses the existing planning audit's scored candidates; it does not
rescore the catalogue or change GFAR. The candidate score combines minimum resulting
per-member relevance averaged over retained visits (weight 0.7), the captured mean
personal score (0.3), and additional geographical detour capped at 20 km (penalty
weight 0.15). All weights are configurable. This keeps quantities bounded and gives
fairness more weight than mean relevance. GFAR selection gain is never treated as
an unconditional POI score. Geographical estimates order search only; trusted directed
route times determine feasibility. A model's suggested candidate is tried first when
no explicit named replacement intent overrides it. Candidates are evaluated in order
until a validated repair is found; this is not an exhaustive optimal replacement search.

Explicit cafe/restaurant requests and named replacement destinations are supported
within the pinned candidate pool. A named destination outside that pool never causes
an arbitrary different destination to be proposed. Additional recognized conditions
without a verified input contract (for example a new accessibility or budget demand)
request clarification instead of silently discarding the condition. Existing group
exclusions, dates, accessibility requirements, route/walking limits, breaks, composition,
duration policy and locks remain authoritative. Preferences are never changed.

## Repair and approval

1. **Fixed neighbors:** preserve every retained start, duration, identity and order.
   Reconstruct incoming/outgoing travel, day boundaries, totals and visit evidence.
   A full stay must fit one dated opening interval. E3 checks the entire result.
2. **Local time adjustment:** shift at most the configured neighbors within the
   minute bound, preserving identities/order and exact locked or started activities.
3. **Same-day CP-SAT:** keep every retained identity mandatory, include the replacement
   and exclude the original. Only the affected day is solved; other dates are merged
   unchanged. Preferred start times minimize total absolute schedule deviation before
   the existing objectives. This optional contract field affects replacement trials
   only. Whole-itinerary E3 still gates publication.
4. **Stop:** no automatic multi-day fallback, dropped activities or relaxed constraints.
   Explain the bounded-search result and offer broader replanning as a separate request.

The private draft stores the base run ID and itinerary hash, state/preference snapshot,
target occurrence, strategy, creation time, complete request/result, evidence and E3
metrics. Public chat receives a draft reference, validated replacement and affected
activity time summary; private preference vectors are not returned.

Approval locks the trip and proposal. SQLite additionally acquires a write reservation,
because it ignores `FOR UPDATE`. The required majority and membership checks are retained.
Expired, legacy unvalidated or stale drafts cannot apply. Refreshed evidence may reject
the draft; if it changes the approved schedule or route accounting, a new vote is needed.
Approval never regenerates a different plan or calls the optimizer. Version publication,
approving members' plan selections, proposal state and result message share one database
commit. A failed commit rolls them back. Repeat approvals are idempotent. The original
itinerary is kept as a separate version; unaffected dates retain their stored content.

## Settings and deployment

| Environment setting | Default | Purpose |
|---|---:|---|
| `REPLACEMENT_CANDIDATE_LIMIT` | 8 | Short bounded pool, maximum 20 |
| `REPLACEMENT_REPAIR_RADIUS` | 1 | Neighbors per side, maximum 3 |
| `REPLACEMENT_SHIFT_MINUTES` | 30 | Maximum shift for flexible retained neighbors |
| `REPLACEMENT_SOLVER_SECONDS` | 2 | Per-candidate same-day solver cap, maximum 30 |
| `REPLACEMENT_TOTAL_SECONDS` | 20 | Entire candidate/enrichment/solver search deadline |
| `REPLACEMENT_VALID_SECONDS` | 600 | Maximum voting lifetime before a new proposal |
| `REPLACEMENT_FAIRNESS_WEIGHT` | 0.7 | Resulting itinerary's minimum member relevance |
| `REPLACEMENT_RELEVANCE_WEIGHT` | 0.3 | Captured mean personal score |
| `REPLACEMENT_DISTANCE_WEIGHT` | 0.15 | Bounded geographical detour penalty |

These bounds limit latency rather than relax feasibility. Existing route and operating
caches remain in the planning-data provider. Approval rechecks selected path edges and
does not fetch an all-pairs matrix. Service HTTP timeouts now distinguish
`agent_request_timeout`, `planning_data_request_timeout` and `optimizer_request_timeout`;
solver UNKNOWN remains a different condition. Agent fallback logs `llm_request_timeout`.
No new retry loop or larger model timeout was added. Diagnostic logs use identifiers,
counts, reason codes, solver status/timing, strategy and validation outcomes rather
than provider payloads, tokens, SQL parameters or preference vectors.

Rebuild **API, optimizer and agent** together to install the new shared code and the
optional `preferred_start_minutes` optimizer field:

```powershell
docker compose up -d --build api optimizer agent
# A built production web image also needs rebuilding; development Vite reloads source.
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build web
```

The agent Docker build now uses repository-root context and copies the shared intent
parser into its standalone image. There are no migrations, automatic imports or
dataset activations in this change. Legacy pending suggestions are shown as stale.

## Verification and measured results

Deterministic tests use synthetic facts and mocked LLM/routing services; integration
tests use the real E2/E3 and CP-SAT with disposable SQLite. They do not contact a live
LLM, routing provider, production database or user chat.

- Local repair/contract/solver focused run: 37 passed (includes the optional deviation
  objective). The final local repair module alone passed 22 cases; its later run
  combined with the API extraction smoke test passed 23 cases.
- Final agent suite: 52 passed.
- Recommender service suite: 18 passed.
- Frontend: TypeScript build and Vite production build passed.
- Root suite: 463 passed, 2 skipped, 1 failed, plus 21 passing subtests in the recorded
  full run. Its `test_travel_objective_can_be_disabled_but_return_still_must_fit` failure
  was reproduced from isolated, untouched HEAD files: the test omits the existing
  `waiting_minutes` phase. No constraint or expectation was weakened to hide it.
- Full API rerun: **147 passed, 2 skipped** (live-service test and opt-in benchmark).
  Final replacement/versioning/membership focused run: **22 passed**, including
  concurrent approval, switching back to the original, started activities and
  two-day publication preservation. The opt-in paired HTTP benchmark also passed.
  One earlier full run had an intermittent 409 in the
  existing adapter-header test; its isolated rerun passed. This is retained as an
  observation, not attributed to a confirmed solver timeout without evidence.

The following regression IDs are covered (several IDs share parameterized tests):

| IDs | Evidence |
|---|---|
| 001, 013, 021 | Shared extraction/resolution; mocked model timeout to relevant rules; ambiguity clarification |
| 002–007, 025–027 | Fixed repair, endpoint nodes, closed/unknown hours, distance, split intervals, unknown routes |
| 008–012 | Candidate iteration, local fallback, day-only mandatory solver trials, no draft on failure, UNKNOWN timeout |
| 014–016, 020 | Budget when enabled, exclusions, locks/started visits and E3 failure |
| 017–019, 023–024, 028 | Majority approval, stale constraints, repeat/concurrent approval, rejection, commit rollback, changed evidence |
| 022 | Pure-engine and API publication checks preserve the unaffected day |

The stored [paired HTTP benchmark](localized_repair_http_benchmark.json) loads the
legacy chat/proposal modules from the recorded HEAD into a separate test app. Five
paired cases share seed 0, member preferences, sample catalogue, operational/routing
fixtures and a two-second solver allowance. A single member votes immediately;
human delay is excluded. Shared underlying services use this checkout. The stored
[engine microbenchmark](localized_repair_benchmark.json) separately measures twenty
two-day repair trials and the legacy full-solve strategy. Neither benchmark is a
production SLA, relevance judgment or user-satisfaction experiment. Preserve raw
rows, per-member utilities, failures and small-sample limitations when reporting.

Final paired HTTP measurements (5 cases per arm):

| Metric | Legacy | Local repair |
|---|---:|---:|
| Median proposal + immediate approval | 1.386 s | 0.143 s |
| p95, nearest rank | 1.559 s | 0.192 s |
| Validated before voting | 0/5 | 5/5 |
| Approved changes applied | 5/5 | 5/5 |
| Full optimizer calls | 5 | 0 |
| Median solver execution | 1.079 s | 0 s |
| Hard-constraint violations after application | 0 | 0 |
| Unrelated activities with changed times, total | 30 | 0 |
| Planning adapter calls per case | 1 | 2 |
| Live external routing API calls | 0 (mocked) | 0 (mocked) |
| Minimum-member rank relevance proxy | 0.6833 | 0.6250 |

The new path makes an extra evidence refresh at approval. Its relevance proxy is
lower in these fixtures, despite better schedule preservation and latency. This is
not evidence of improved recommendation quality or satisfaction. These one-member
cases cannot establish multi-member fairness; full raw per-member fields remain in
the JSON. LLM timeout rates are not measured by this benchmark. Failure-preservation
rates are tested with separate injected-failure regressions, not inferred from these
successful cases.

The 20-case two-day engine comparison measured median **39.84 ms → 1.44 ms** and
p95 **45.20 ms → 2.00 ms**. Both arms produced 20/20 E3-valid results and zero
hard-constraint violations. The baseline moved two unrelated activity start times
per case; local repair moved none. Both recorded zero solver timeouts. These
microbenchmarks exclude HTTP, persistence, enrichment and voting latency. Artifact
metadata records source hashes, seed, fixture hash and baseline revision.

## Files in this implementation

Added:

- `api/app/replacements.py`: shared backend gate, evidence refresh, private draft and exact-schedule publication.
- `src/ourney_travelsync/replacement_intent.py`: shared conservative intent and occurrence resolution.
- `src/ourney_travelsync/optimizer/repair.py`: fixed/local reconstruction and same-day request reduction.
- `tests/test_localized_repair.py`, `api/tests/test_replacement_validation.py`,
  `services/agent/test_replacement_fallback.py`: deterministic regression coverage.
- `api/tests/test_replacement_benchmark.py`, `scripts/benchmark_localized_repair.py`:
  paired HTTP and engine benchmark runners.
- `docs/localized_itinerary_repair.md`, `docs/localized_repair_benchmark.json`,
  `docs/localized_repair_http_benchmark.json`: architecture, results and raw evidence.

Updated:

- `api/app/config.py`, `api/app/services.py`: replacement bounds and distinct HTTP timeouts.
- `api/app/proposals.py`, `api/app/routes/chat.py`, `api/app/routes/itineraries.py`:
  pre-vote gate, transaction, selected-version consistency, idempotence and preserved versions.
- `src/ourney_travelsync/optimizer/contracts.py`, `src/ourney_travelsync/optimizer/solver.py`:
  optional preferred starts and replacement-only deviation objective. Concurrent
  provisional-hours additions in the contracts file were preserved.
- `api/tests/test_proposal_approval.py`, `api/tests/test_membership.py`: corrected
  pre-vote expectations, deterministic fixture clock/seed and deletion coverage.
- `services/agent/brain.py`, `services/agent/Dockerfile`, `docker-compose.yml`:
  relevant fallback, accurate hours summaries, timeout logs and shared-parser packaging.
- `web/src/types.ts`, `web/src/pages/Chat.tsx`, `web/src/i18n.ko.ts`: validated status,
  actual time changes, approval progress wording and Korean labels.
- `.env.example`, `.gitignore`, `README.md`, `docs/optimizer_app_integration.md`,
  `services/agent/README.md`: settings, scratch-output exclusion and architecture links.

Concurrent provisional-schedule/data-import edits observed during execution are
separate work and were preserved. No commit, push, deployment, production data import
or activation was performed.

Run the checks and reproduce the benchmarks from an environment with API, optimizer,
agent and recommender dependencies installed:

```powershell
.venv/Scripts/python.exe -m pytest tests api/tests -q
# Run agent separately because its fixture database and import names are service-local.
Push-Location services/agent
../../.venv/Scripts/python.exe -m pytest -q
Pop-Location
.venv/Scripts/python.exe scripts/benchmark_localized_repair.py --output docs/localized_repair_benchmark.json
$env:TRAVELSYNC_RUN_REPLACEMENT_BENCHMARK='1'
.venv/Scripts/python.exe -m pytest api/tests/test_replacement_benchmark.py -q
Remove-Item Env:TRAVELSYNC_RUN_REPLACEMENT_BENCHMARK
```

## Manual reproduction and limits

1. Rebuild the services above. Use a test trip with agreed future dates, preferences,
   availability, start/end points and a published E3-checked itinerary.
2. If the itinerary actually contains 경성대학교 콘서트홀 on November 30 at 19:08,
   send `@TravelSync 경성대학교 콘서트홀 가기 싫어 다른 곳으로 치환해줘.`
   Otherwise use the actual scheduled name; do not insert fabricated operating facts
   into the active dataset merely to reproduce this example.
3. Confirm either a validated draft with an exact change summary or a relevant
   non-votable explanation. Unknown/provisional candidate hours must not yield voting
   buttons. Failed searches leave the original unchanged.
4. Approve with the existing majority. Check the published new version and preserved
   original; inspect logs for the proposal ID and strategy. There must be no solver
   invocation during approval. Repeat approval and confirm no duplicate version.
5. Repeat after changing the plan/constraints during voting. Expect STALE and a new
   proposal requirement. Controlled external-service failures belong in a disposable
   test environment, not the production database.

The isolated named/time regression is
`tests/test_localized_repair.py::test_original_korean_hall_request_at_1908_with_labeled_evidence`.
Candidate search is intentionally limited to the pinned planning pool. Broader catalogue
retrieval and unknown/unsupported new conditions can require a separate request.
Same-day repair uses the verified graph already available plus the replacement's two
new edges; missing alternative edges can limit search completeness. A failure therefore
means no validated replacement was found within these limits, not proof that none exists.
The local deterministic repair is a bounded heuristic and can defer feasible cases to
the solver. PostgreSQL row-lock behavior is implemented but has not been exercised
against a live PostgreSQL server in this task; concurrency tests use SQLite.
No live external-service benchmark or historical production replay was performed.

## 시스템 통합 테스트 및 개선 사항

장소 교체 요청은 그룹 투표 전에 운영시간·이동시간·기존 제약조건과 전체 일정을 검증하도록
개선했다. 기존 일정의 빈 시간에 먼저 대체 장소를 배치하고, 필요한 경우 주변 시간이나 해당
날짜만 제한적으로 조정한다. 검증된 변경안을 저장하므로 승인 시 전체 일정을 다시 최적화하지
않으며, 일정 변경·동시 승인·저장 실패 상황에서는 기존 일정과 버전을 보호한다.
한국어 교체 요청은 LLM 응답 지연 시에도 관련 없는 일반 안내로 대체되지 않도록 처리했다.
성능 수치는 합성 데이터와 모의 외부 서비스를 사용한 로컬 테스트 결과이며 실제 사용자 만족도나
운영 서비스의 응답속도를 의미하지 않는다.
