# POI enrichment import — 21 November 2026

The local database now uses `busan-enriched-v4-20261121`, an immutable snapshot
of all 3,570 POIs. It incorporates usable official API descriptions and strictly
normalized schedules from the latest collection, including the newly authorized
Busan Attraction API. The previous V3 dataset and historical recommendation runs
remain available unchanged.

| Result | Count |
|---|---:|
| POIs with newly incorporated description text | 640 |
| Previously missing descriptions filled | 15 |
| Existing descriptions supplemented | 625 |
| POIs with a nonempty stored description after import | 2,267 |
| Descriptions eligible for the source-backed E5 text path | 2,264 |
| Supported recurring schedules in the new normalized batch | 74 |
| Prior normalized schedules retained in the new batch | 72 |
| Additional normalized schedules | 2 |
| Hours-bearing POIs held for review | 590 |
| Source time-range disagreements | 38 |
| Collected source-evidence rows retained in the database | 1,388 |
| POIs with at least one review item or missing field | 3,496 |

The two additional schedules belong to 부산시민공원 (`kakao:11585715`) and
송도해수욕장 (`kakao:25041637`). The 74 schedules are a subset of the 640 POIs
whose descriptions were supplemented; these counts must not be added together.
The full collection had hours text for 664 POIs and descriptions for 679. The
449 preliminary schedule candidates were not import approvals: the stricter
normalizer accepted 74, with explicit supported hours and closure policies.

## Category breakdown

Classification gives café precedence over food, then restaurant, with other
nonfestival POIs grouped as attractions. Festival-only POIs are shown as other.

| Category | Catalogue | Descriptions supplemented | Previously missing descriptions filled | Normalized schedules | Hours held |
|---|---:|---:|---:|---:|---:|
| Attraction | 1,881 | 259 | 8 | 20 | 229 |
| Restaurant | 841 | 262 | 3 | 37 | 255 |
| Café | 799 | 98 | 2 | 17 | 86 |
| Other / festival | 49 | 21 | 2 | 0 | 20 |
| Total | 3,570 | 640 | 15 | 74 | 590 |

## Import and review rules

Only conservatively matched official-source evidence with passing content
validation was eligible. Current source time conflicts, uncertain identity, and
existing metadata marked `CONFLICT` blocked new usable enrichment for that POI.
Original descriptions were retained and unique official-source text appended
verbatim; endpoints, source IDs and original retrieval dates accompany it.
The data is marked source-matched, not independently venue-verified.

Ambiguous opening hours did not prevent an otherwise usable description from
being incorporated. They did prevent a normalized schedule import. Seasonal and
conditional rules, cutoffs, last orders, unknown closures, festivals, unsupported
weekday expressions, overnight hours and source disagreements remain reviewable.
No generic “always open” prose was promoted to a weekly schedule.

The hours-review reasons are mutually exclusive first-failure classifications:

| Reason | POIs |
|---|---:|
| Conditional, seasonal or admission-cutoff rule | 295 |
| No explicit time range | 99 |
| Closure policy unknown | 53 |
| Source time-range disagreement | 38 |
| Existing last order needs review | 29 |
| Festival is not a recurring schedule | 20 |
| Day-specific or 12-hour rule unsupported | 18 |
| Unsupported closure rule | 13 |
| Unparsed qualifier | 13 |
| Identity review required | 5 |
| Overnight or equal-time range | 5 |
| Existing break needs review | 2 |
| Total | 590 |

The database retains all evidence in `poi_enrichment_evidence` and all per-POI
review payloads in `poi_enrichment_reviews`, scoped to the new dataset. Their
flags distinguish usable descriptions, usable schedules and held evidence.
The review count includes missing fields and POIs whose descriptions were
imported but whose hours still require review; it is not a count of conflicts.
For 51 POIs with source/identity conflicts (including pre-existing metadata
conflicts), the cloned dataset suppresses legacy known-hours flags. The original
datasets, global legacy hours and dated exceptions are preserved.

## Recommender and optimizer integration

Local `.env` deployment settings now select the V4 CSV and version for API and
recommender. Both containers were recreated. New recommendations pin the active
dataset and send its database metadata to the recommender. The optimizer's
candidate catalogue and planning-data requests use that same pinned version.
Existing runs continue to reference their historical datasets; regenerate a
recommendation to use V4 for an existing trip.

E5 embeddings must match the enriched scoring text. A new cache was prewarmed
using the already downloaded pinned `intfloat/multilingual-e5-small` checkpoint,
without a model download. Ordinary requests retain the existing cache-only
policy. The original V3 and operating CSVs were not edited.

Normalized hours are stored in `poi_normalized_hours` under
`hours-busan-enriched-v4-20261121`, effective 21 November. Planning data, POI
details and availability already read this version-scoped table. Dated exceptions
retain precedence. No scoring formulas, solver constraints, route provider or
enforcement settings were changed. The current app enforces known opening hours
and permits unknown hours; budget enforcement remains disabled. Unknown hours
therefore still limit claims about itinerary feasibility.

## Verification and reproducibility

The import committed dataset, metadata, schedules, evidence, review records and
activation in one transaction after source-hash and parent-identity checks.
Readback matched the prepared metadata and schedules. The parent POI/metadata
fingerprint, global hours/exceptions, 45 historical recommendation runs and five
itineraries were unchanged. A pre-import PostgreSQL data backup is retained.

- 93 focused tests passed, covering conflict exclusion, transactional rollback,
  history preservation, normalized hours, collection, E5 and planning data.
- All 74 schedules matched the actual running planning-data container across
  seven days: 518 POI/date checks.
- The live recommender returned five results from the enriched DB snapshot;
  the final verification requires actual E5 hybrid scoring without fallback.
- The live optimizer used an imported schedule and returned `OPTIMAL` with
  independent validation `PASSED`. This controlled check uses co-located
  endpoints and explicitly labeled zero-distance test routes; it does not
  establish external routing coverage or real-world venue accuracy.
- New artifacts were checked for credential leakage and packaged source hashes
  were rechecked. No commits or remote deployment were performed.

Artifacts are in `output/enrichment_import_20261121/`: `manifest.json`,
`import_result.json`, `verification.json`, `review_queue.csv`, the V4 CSV,
`database_before_import.dump`, and live recommender/optimizer smoke responses.
The deployable CSV is also under `src/ourney_travelsync/data/`.

The explicit import command was:

```powershell
.\.venv\Scripts\python.exe -B scripts/promote_poi_enrichment.py --out output/enrichment_import_20261121 --apply-local
```

Do not rerun that command to overwrite V4: dataset versions are immutable.
For a later promotion, prepare a distinct output directory and version first.
Daily collection remains collection-only; it does not automatically activate
new evidence or bypass review. Resolve the retained conflicts before another
explicit promotion.
