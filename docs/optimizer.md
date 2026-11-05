# Module H — itinerary optimizer

Implements TS SRS 1.1 FR-ITIN-OPT-01 and SDD 1.1 sections 10–11. Source
specifications inspected: `TravelSync_요구사항명세서_v1.1.txt` and
`TravelSync_상세설계서_v1.1.txt` in the project-chat workspace (2026-10-07).

## Contract and formulation

`optimizer.contracts.PlanRequest` is the strict `optimizer-1.0` contract. It
pins run, dataset, preferences, constraints, mapping, schema, operating snapshot
and routing versions. Times are integer local minutes on each supplied date;
money is integer KRW. An adapter must ceil travel/dwell durations, expand overnight
hours into dated windows, split lunch breaks, and resolve dated holidays and
event validity. Every member supplies dated availability, original personal
scores and GFAR relevance. Unknown facts remain null with UNKNOWN status.
Zero cost/travel is accepted only as an explicit evidenced input.

Per day: optional visit booleans y, integer service starts t, directed arc
booleans a. AddCircuit includes fixed end→start, no endpoint self-loops, and
POI self-loops equal to NOT y. Real selected arcs enforce time propagation,
including the last return leg; waiting is allowed. Exactly one complete opening
interval must contain each selected stay. Across days sum(y)≤1. Each requested
day must contain a visit. Costs include party size × per-person visits plus
group fares on every selected real edge. Max-leg and per-day walking limits
apply. Member exclusions are unioned and availability windows intersected.

One shared search budget ≤30 seconds covers all stages. Maximize fixed GFAR
group_utility, then preserve the incumbent utility and maximize minimum member
relevance (×10,000 rounded), then preserve both and minimize travel minutes.
Record raw status, objective, bound and allocated/actual time per search.
FEASIBLE never claims optimality. A later timeout must retain the incumbent.
E3 rechecks raw snapshots and route records independently before publication.

Every stage has a fixed fraction of the attempt cap (70% / 20% / 10%); the
allocated caps sum to at most that cap. An elapsed deadline also limits later
phases. Early termination leaves budget unused. CP-SAT time limits are
cooperative, so process teardown can slightly exceed a configured limit; this
is a search budget, not a hard 30-second HTTP wall-clock guarantee. Model
construction, parsing and E3 are outside the reported solver search totals.
An OPTIMAL response means all three objectives were proved; otherwise an
incumbent is FEASIBLE, with each raw phase status retained. Timeout without an
incumbent is UNKNOWN. Model validation failure is MODEL_INVALID.

`optimize_run(PlanRunRequest)` / `POST /runs` accepts an initial and at most one
pre-enriched expanded snapshot. With an expansion, the first attempt receives
half the total cap; a retry receives only the remaining unallocated time, also
bounded by elapsed time. Retry is allowed only for candidate shortage, no
feasible itinerary, or no solution before timeout. A valid incumbent is kept.
Run versions, members, exclusions, locks, dates, budget, mode and limits must
remain identical. Both attempts retain their input, original GFAR fields, E2
results and solver phases. The caller must save the original run envelope too.

Daily departure is fixed as SDD 11.3 requires. Each member must be available at
that instant, and the whole route must fit in the continuous common availability
interval containing it. Overlapping/adjacent windows are unioned. An unavailable
departure returns CONSTRAINT_CONFLICT; selecting a later departure requires a
new explicit request. Trips cannot cross an unavailable midday gap.

KNOWN facts require values, sources and verification timestamps. Dwell estimates
also require a rule version; cost estimates require an explicit user upper bound.
Required accessibility must be known and true. Festivals require known dated
event validity. A route needs known duration, distance, group fare and walking
minutes plus provider and fetch timestamp. Unsupported/unknown fares must not
be supplied as zero. The supported budget scope is all supplied per-person POI
fees and group route fares; accommodation, meals or other spending must be
explicitly included by the adapter if they are part of the user's budget.

## Integration boundary

The legacy `/plans` mock and `plans.*.json` describe three themed greedy plans.
They cannot supply routing, party budget, common availability or verified
operating snapshots. The new versioned service must reject that old payload.
The app must collect these inputs and call the new service explicitly; switching
OPTIMIZER_URL alone is insufficient. No production records or schema are changed.
No synthetic facts are used by the implementation.

Candidate expansion belongs to the recommendation/enrichment orchestrator: it
must retain original GFAR ranks/utilities, expand at most once (L=200, K=60),
pin the new snapshot, and share the remaining run search budget. Routing provider
integration and time-dependent route refresh also belong to that orchestrator.
These production dependencies are not evidence of end-to-end feasibility.

## Calling and running

Install `uv sync --locked --extra optimizer`. For HTTP also install
`uv pip install -r services/optimizer/requirements.txt httpx`, then run
`uv run --no-sync uvicorn main:app --app-dir services/optimizer --port 8003`.
`GET /health`, `/docs` and `/openapi.json` describe the service.
`POST /plans` accepts one PlanRequest; `POST /runs` accepts
`{"initial": PlanRequest, "expanded": PlanRequest-or-null}`. Direct Python calls
are `optimizer.api.optimize` and `optimizer.api.optimize_run`.

Invalid contract data returns HTTP 422 / INVALID_INPUT without echoed values.
Domain outcomes return HTTP 200 with explicit status and error_code:
CONSTRAINT_CONFLICT, INSUFFICIENT_CANDIDATES, NO_FEASIBLE_ITINERARY,
SEARCH_TIMEOUT, INVALID_SOLVER_MODEL or FINAL_VALIDATION_FAILED.
Unsolved and E3-failed results contain no days. E3 success exposes route times,
return leg, party costs, per-member utilities and disparity, solver settings,
phase objectives/bounds/timings and the complete versioned input snapshot with
SHA256. Relevance metrics are scorer-derived, not observed user satisfaction.

## Production integration requirements

1. An authenticated orchestrator must load the captured group recommendation,
   preferences/constraints and dataset versions. Convert original personal scores
   to the documented 0–1 scale; supply all members' GFAR relevance separately.
   Keep selection_gain, mean_score, group_rank and group_utility distinct. Retain
   the actual candidate count and original utility formula before E2 exclusions.
2. Enrich only those candidates, resolve holidays/exceptions and festival dates,
   retain field evidence, and expand overnight/split schedules into dated
   intervals. Hours tables keyed only by POI ID are not historical snapshots.
   The existing hours parser's combined lunch-break range is insufficient input.
3. Resolve endpoint IDs to coordinates and obtain directed routing for the
   selected mode and reference departure times. Provide conservative static
   durations or externally selected time buckets. This solver accepts one frozen
   matrix per day. If route refresh invalidates E3, return a refresh-needed state
   or replan only within the remaining run search budget; never reset 30 seconds.
4. Use the `/runs` envelope if a once-expanded GFAR pool is available (L≤200,
   K≤60). This module budgets and records attempts but does not query the
   recommender, enrichment cache or routing provider itself.
5. Call the new service with a transport timeout above 30 seconds plus parsing,
   model-building and validation overhead. The current API default is 10 seconds;
   async job lifecycle and progress reporting remain integration work.
6. Store request, result, phase states and runtime/code/lockfile versions against
   the run. Publish only validation_status=PASSED. The existing database storage
   adapter accepts trusted optimizer output but is not itself E3. No schema
   migration is necessary for this standalone module. The legacy app's A/B/C
   view needs an explicit mapping/new view for the single optimized itinerary.

The service is internal and has no authentication layer. Keep it behind the
authenticated backend; input snapshots and per-member metrics require access
control and must not be sent wholesale to group-facing public screens.

## SDD alignment and boundaries

- Implemented: dated AddCircuit model, optional visits, one visit minimum per
  day, no repetition, waiting and split hours, E2 evidence exclusions, hard member
  rules, directed routing and return, party budget, accessibility/events, movement
  limits, lexicographic objectives, replay snapshots, one budgeted expansion and
  independent E3 publication gate. No default constraints are relaxed.
- Contract adaptation: endpoint IDs refer to upstream resolved locations instead
  of embedding coordinates in every solver request. Member availability is
  explicit, and service calls are synchronous. Legacy three themed variants are
  retained only in the existing mock; Module H returns one optimized itinerary.
- Not wired here: recommendation expansion generation, enrichment/provider calls,
  route refresh, async jobs, production persistence/UI publication. These are
  integration dependencies; the current app still uses the legacy mock.
- Acceptance remains unverified: real-provider/operating-data freshness and
  correctness, deployment startup, live authenticated optimized trip flow,
  performance at 10,000 source POIs / prescribed 20-run fixed-environment p95.
  Solver input is bounded to 60 enriched candidates as SDD specifies.

## Validation record

See [optimizer verification report](optimizer_verification.md) for commands,
results, changed-file summary and remaining assumptions. Tests use isolated
synthetic inputs, an exhaustive small-instance oracle, HTTP contract checks and
adversarial E3 mutations. Existing production POI data and database contents are
not optimizer test fixtures.

References: [Google CP-SAT statuses](https://developers.google.com/optimization/cp/cp_solver)
and [solver limits](https://developers.google.com/optimization/cp/cp_tasks).
