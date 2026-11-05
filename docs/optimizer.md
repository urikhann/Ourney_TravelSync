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
