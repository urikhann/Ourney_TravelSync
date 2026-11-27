# Validated chat replacements

Follow-up: [structured confirmation, lock provenance and planning scopes](conversation_state_and_replanning.md).

The implementation uses the existing GFAR recommendation snapshot, planning-data
adapter, E2 checker, E3 validator, private optimization audits and group votes.

## Execution path

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

## Verification and limits

See [test commands](optimizer_verification.md). Regression coverage includes fixed
and local repair, same-day solver fallback, stale inputs, concurrent approval,
rollback, locks, unknown hours and preservation of unaffected dates.

Candidate search is limited to the pinned planning pool. Same-day repair uses the
available verified graph and the replacement's two new edges. Missing edges and
bounded search can prevent a feasible replacement from being found. A failed
search preserves the current itinerary; it does not prove no replacement exists.
Synthetic regression fixtures and local microbenchmarks do not establish live
provider performance or user satisfaction. Benchmark runners remain in `scripts/`
and the opt-in API tests; save generated results under ignored `output/`.
