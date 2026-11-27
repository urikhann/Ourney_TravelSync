# Itinerary optimizer

See [app integration](optimizer_app_integration.md) for service configuration and
[meal scheduling](dynamic_meals.md) for daily composition and overrides.

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
including the last return leg; waiting is allowed. When hours enforcement is
enabled, each selected stay must fit a complete
known opening interval. Unknown hours require `allow_unknown_opening_hours=True`;
known closures and intervals remain binding. Across days sum(y)≤1. Each requested
day must contain a visit. Costs include party size × per-person visits plus
group fares on every selected real edge. Max-leg and per-day walking limits
apply. Member exclusions are unioned and availability windows intersected.

Gap filling also checks unused time between the planned return and the day's
end or shared availability limit. It can append an unused activity or café while
preserving existing visit times. Driving plans obtain the additional road legs
before accepting a stop: travel from the last visit, the full new stay, and
travel to the end location must all fit. Opening hours, breaks, exclusions and
daily composition still apply. Existing objective priorities are preserved;
delaying visits alone is not an improvement. If no validated addition fits,
the existing plan is retained. End-of-day unused time is not reported as
waiting between visits.

One shared search budget ≤30 seconds covers all stages. Maximize fixed GFAR
group_utility, then preserve the incumbent utility and maximize minimum member
relevance (×10,000 rounded), then preserve both and minimize travel minutes.
Requests with `optimize_travel=False` omit travel minimization; travel still
constrains feasibility. Meal preferred starts add a final deviation objective.
Record raw status, objective, bound and allocated/actual time per search.
FEASIBLE never claims optimality. A later timeout must retain the incumbent.
E3 rechecks raw snapshots and route records independently before publication.

Default stages receive 70% / 20% / 10% of the attempt cap; the
allocated caps sum to at most that cap. An elapsed deadline also limits later
phases. Early termination leaves budget unused. CP-SAT time limits are
cooperative, so process teardown can slightly exceed a configured limit; this
is a search budget, not a hard 30-second HTTP wall-clock guarantee. Model
construction, parsing and E3 are outside the reported solver search totals.
An OPTIMAL response means all configured objectives were proved; otherwise an
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

Daily departure is fixed. Each member must be available at
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
operating snapshots. The versioned optimizer rejects that payload.
The app collects these inputs, loads pinned recommendation/member data, and
calls the optimizer explicitly; switching OPTIMIZER_URL alone is insufficient.
Its trusted planning-data adapter must be configured before it can schedule
real places. No synthetic facts are used by the implementation. The necessary
additive API audit table is documented in the app integration guide.

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

## Integration requirements

The authenticated API supplies pinned scores, member rules, dates, endpoints,
operating facts, and directed routes. It stores private request/result snapshots
and publishes only after local E3 validation. The solver service is internal and
does not authorize end users. Do not expose its per-member metrics or complete
input snapshot on public group screens.

The app uses the [Kakao planning-data adapter](../services/planning_data/README.md).
It schedules one bounded candidate pool. Automatic expansion, traffic refresh,
and asynchronous job management are not performed by the solver.

Use transport timeouts that cover search, model construction, validation, and
network overhead. The API defaults to a 45-second optimizer timeout; Compose's
planning-data timeout is 180 seconds for batched route calls.

See [testing commands and coverage](optimizer_verification.md).
References: [CP-SAT statuses](https://developers.google.com/optimization/cp/cp_solver)
and [solver limits](https://developers.google.com/optimization/cp/cp_tasks).
