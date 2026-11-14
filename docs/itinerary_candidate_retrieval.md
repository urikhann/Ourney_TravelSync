# App itinerary candidate retrieval

The Plan action retrieves from every POI in the recommendation run's pinned dataset, using that run's frozen member preferences and exclusions. The displayed recommendation list is unchanged. The recommender scores the complete catalogue before exclusions and day selection, preserving E5 percentile scope. It then applies E1 exclusions, retrieves bounded day pools, and GFAR ranks their union with every member's scores and relevance retained.

## Day retrieval policy: catalogue-day-clusters-v1

- One/two/three days use at most 40/30/20 candidates per day; the union never exceeds the optimizer's 60-place contract. These are routing/computation bounds, not a 30-place catalogue limit.
- A straight-line start -> POI -> end lower bound at an optimistic 130 km/h plus a supplied known/estimated dwell duration removes obviously impossible visits. Unknown durations are retained for enrichment; the optimizer still requires supported duration evidence. This geographic screen does not establish route availability or travel time.
- Selection combines group mean/minimum preference relevance, detour from the endpoint corridor, proximity to a preference-weighted geographic anchor, per-member category requests (ratings >=4), mild activity coverage, and a penalty for candidates already nominated on an earlier day. Weights are engineering heuristics, not validated relevance improvements. Retrieval category coverage supplies candidate opportunities; app-level meal/activity constraints are independently enforced below.
- Locks are included in their requested day before other candidates. Unknown/excluded locks are rejected. A lock that cannot fit the geographic/dwell screen is rejected by the retrieval service.
- Per-day membership is included in the private audit, routing request, typed optimizer candidates, E2, and independent E3. The route adapter fetches only that day's candidates. Existing global no-repeat and route/time checks remain.

## Explicit app test-mode bypass

Opening hours, closure checks, and budget limits remain bypassed (`enforce_opening_hours=False`, `enforce_cost=False`) in app planning requests. Source fees/hours are preserved, unknown fees are not relabeled free, and the UI labels the test mode. Strict standalone optimizer defaults are unchanged. Festival-date verification, required accessibility, member availability, exclusions, durations, and verified route requirements remain active.

## Daily composition for new app plans

`GenerateInput.daily_composition` defaults to at most two cafe visits, two to three meals, at least two activity visits, and at least two distinct activity categories per day. Food/drink visits cannot be consecutive: an activity separates each pair. All three meal windows are optional choices, with at most one meal in each, but at least two must be used: breakfast 07:00-11:00, lunch 11:00-15:00, dinner 17:00-21:00. The full meal stay must fit its window. These are requested itinerary structure windows, not claims about a restaurant's opening hours.

Roles are exclusive: the imported cafe feature takes priority over food, so cafe/food multi-label places do not satisfy a meal requirement. Other food-feature places count as meals. Remaining POIs count as activities with one primary category from the imported feature order (nature, history, art, shopping, activity, entertainment, relax, festival; otherwise other). A multi-label activity counts once toward category diversity. This uses existing feature classifications; it is not independently verified menu or venue evidence.

Candidate retrieval first reserves restaurant alternatives (normally six), activity alternatives across categories, and cafe alternatives before filling by preferences. Member exclusions and geographic/dwell screening apply first. This avoids a cafe-heavy preference list crowding out all restaurants. The bounded pool and its role counts remain in the private audit.

CP-SAT enforces all counts, meal assignment/windows, and food/drink separation. Independent E3 reconstructs the same checks from selected visits, without trusting solver eligibility or stored totals. Impossible composition, insufficient candidates, short days, or conflicting cafe locks cause failure rather than silent relaxation. Standalone optimizer requests default to no composition policy for backward compatibility; the app explicitly supplies the policy. Existing saved plans and votes are not rewritten: rebuild to apply the new policy. Published items carry their role/category and the UI shows per-day counts and breakfast/lunch/dinner labels.

## Audit and limitations

`itinerary_optimization_runs.input.candidate_retrieval` records the returned GFAR candidates, individual scoring diagnostics, full/eligible catalogue counts, policy version, per-day membership, cluster anchors, and category coverage. Published items load details from the pinned dataset even when absent from the displayed recommendations. The UI shows catalogue and selected-pool counts without exposing individual scores.

This is bounded heuristic retrieval followed by CP-SAT and independent validation. It does not optimize over every catalogue POI simultaneously, automatically expand failed pools or verify future traffic/hours. Unknown facts remain unknown. Synthetic tests establish software behavior, not real-trip satisfaction or operating feasibility.

## Verification

Offline tests cover full-catalogue retrieval beyond the displayed/top-L list, distinct day locations, activity nominations, duration filtering, exclusions, locks, deterministic output, unchanged saved recommendations, app bypasses, day-specific routing, E2/E3 membership enforcement, and authenticated publication. Tests use isolated SQLite data and mocked operating/route evidence; no live trip or Kakao request is required.

Verified on 2026-11-13: the regression command spanning API tests, optimizer API/E2/E3 tests, Kakao adapter tests, and recommender service tests reported 202 passed and one skipped (the opt-in process test). The frontend TypeScript/Vite production build passed. A public-catalogue smoke run with synthetic feature-mean preferences reviewed 3,570 POIs and selected 60 candidates (20/day) in 0.459 seconds; that timing excludes routing and optimization. A read-only local-container retrieval using the saved trip inputs reviewed 3,570 rows, retained 3,519 after exclusions, selected 57 distinct candidates (20/day), and included 42 beyond its displayed recommendation list. That check sent no Kakao requests and wrote no trip data; a live itinerary was not generated by this verification.

Daily-composition verification on 2026-11-13: 220 regression tests passed; one opt-in process test was skipped. The frontend production build passed. An installed-container synthetic fixture returned OPTIMAL with three meals, two cafes, and five activities, and passed independent API validation. A read-only retrieval using the saved trip inputs reviewed 3,570 POIs and retained six meal alternatives, nine activity alternatives, and five cafe alternatives per day (candidate counts, not scheduled visit counts). Neither check changed a saved itinerary or made Kakao requests. Rebuild a live itinerary to apply the new composition policy.

## Persistent demo route cache (no expiry)

The planning-data service stores successful directed edges in SQLite at `/cache/routes.sqlite3` on the dedicated Compose `route_cache` named volume. Normal restarts, image rebuilds, and container recreation preserve this volume. Removing the volume (including `docker compose down -v`) removes its routes. No TTL, date-based eviction, background refresh, or cache-hit provider request is used. The original provider name and `fetched_at` are preserved; these are saved observations, not current traffic verification or forecasts for a future trip date.

Keys include exact longitude/latitude for both endpoints, direction, driving mode, priority, toll avoidance, batch radius, and routing policy version. Trip IDs, POI IDs, and trip dates do not change an otherwise identical route. Coordinate or policy changes cause a miss. Only valid KNOWN routes are persisted; UNKNOWN/UNREACHABLE results, malformed data, credentials, quota errors, and HTTP errors are not cached. A cache access failure stops generation rather than silently spending quota. Simultaneous overlapping origins share requests within the service process. Different service replicas/processes share disk entries but do not provide cross-process single-flight coordination.

Only uncached legs are sent to Kakao. Successful earlier batches are saved even if a later batch fails. Unseen routes still require available Kakao quota; caching cannot manufacture those routes. The app's existing operating/cost bypasses and daily composition checks are unchanged.

To seed already observed routes without using Kakao: `docker compose exec -T planning-data python seed_route_cache.py`. This reads local saved planning audits and writes only the cache volume. It accepts successful edges from the matching adapter routing version and historical default radius 5000, preserving their original observation timestamps. It does not alter trips, votes, audits, preferences, or POI data. Audits with no saved successful response cannot be recovered this way.

Cache verification on 2026-11-14: 40 offline provider/cache and API integration tests passed. Tests cover no-expiry reuse years later, a fresh module using the same cache file, original timestamp preservation, direction/coordinate/policy isolation, concurrent deduplication, partial misses, failed-result exclusion, local audit seeding, and fail-closed storage errors. The deployed seeder processed 11,590 successful saved edge observations into 3,647 distinct cached route keys. After an actual planning-data container restart, a cached route was returned for renamed nodes and a different date using a MockTransport that blocks every provider request. No Kakao requests were made during implementation or validation.
