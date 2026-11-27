# V5 in-place provisional schedule import — 26 November 2026

All 334 preliminary POIs were imported as structured provisional schedule
records into the existing `busan-enriched-v5-20261125`. No new dataset version
was created. The database, `enrichment_v5_manifest.json.gz`, and deployment
specification now agree on the amended V5 content. The V5 CSV and its metadata,
POI identities, features, and descriptions were unchanged; schedule data belongs
to the deployment manifest and version-scoped hours table.

| Schedule class | Records | Planner behavior |
|---|---:|---|
| Existing normalized schedules | 73 | Existing known-window behavior retained |
| Provisional schedules with ordinary windows | 88 | Estimated windows constrain visits when unknown/provisional hours are permitted |
| Provisional schedules with unresolved conditions | 246 | Source rules retained; no invented weekly window |
| Total schedule records | 407 | 73 normalized + 334 provisional |

All 334 were added to `poi_normalized_hours` under the existing V5 hours batch,
using `provisional-hours-1.0` for the new entries. This table now contains both
normalized and provisional schemas; its total row count is not the number of
confirmed weekly schedules. The existing batch count/hash and installation
specification were updated. Existing evidence rows retain their original text,
review reasons and status, plus a provisional-schedule-import marker. All review
records and the 42 excluded candidate POIs were preserved. The 42 were not part
of this import.

Every provisional record retains its source rules, extracted reported ranges,
source IDs, original retrieval dates, unresolved conditions, and review flag.
`independently_verified` remains false. Unsupported seasons, holiday exceptions,
subfacility scope, day-specific rules and overnight expressions stay unresolved.
Historical October break and last-order snapshots were not silently relabeled
as recurring. Current explicit breaks remain separate windows; an explicit
admission/last-order cutoff conservatively limits the whole visit to finish by
that time. The record states this conservative restriction.

Expandable provisional windows are emitted as `ESTIMATED`, with a provisional
source and rule version. Unknown closure policies are not labeled confirmed
open days. E2 limits candidate visits to the estimated intervals under the
app's existing allow-unknown policy, and independent E3 enforces the same
bounds. Strict requests that reject unknown operating information continue to
reject these estimates. Unresolved provisional records do not fall back to
stale weekly hours. POI detail and availability responses expose uncertainty;
strict breakfast-serving verification and repair eligibility remain unchanged.

## Verification

- 203 focused and deployment tests passed (one optional test skipped): provisional parsing, split breaks/cutoffs, unknown
  conditions, strict-policy rejection, E2/E3 window checks, legacy schedules,
  planner data, meal rules, solver, and service contracts.
- Live planner readback checked all 407 schedule records over seven days:
  2,849 POI/date checks. Results were 511 known, 616 estimated and 1,722
  unresolved POI/days, matching the source-preserving schedule records.
- A live optimizer check using actual provisional operating facts returned
  `OPTIMAL`, independent validation `PASSED`, and an unverified-hours warning.
  Its route edges and preference profile were explicit controlled fixtures;
  this was not an external routing or real-group quality evaluation.
- Full API startup validation passed against the amended manifest and database.
  API, recommender, planning-data and optimizer health checks passed. The
  recommender retained E5 hybrid scoring without fallback; descriptions and
  scoring-cache identity were unchanged.
- The import transaction checked that all POI/metadata records, dataset-version
  rows, saved recommendation runs and itineraries, review payloads, frozen
  weekly/date snapshots and the 73 prior normalized schedules were unchanged.
  No dataset was created, archived or activated by this amendment.

The pre-amendment manifest, deployment specification, and PostgreSQL backup are
retained under `output/v5_provisional_schedules_20261126/`, alongside
`import_result.json`, `verification.json`, and optimizer request/response
artifacts. The backup contains local database data and must stay private.

This is an explicitly requested in-place amendment. V5's identifier therefore
refers to amended hours on a new planning request, including when replanning an
older V5 recommendation. Previously saved request/response rows were not
rewritten. Use the retained original manifest and backup when reconstructing
the pre-amendment hours. Daily collection still does not auto-import or modify
these schedule records.
