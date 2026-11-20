# Agreed planning choices — 2026-11-17

Implemented application scope: 1 B, 2 B, 3 A, 4 B, 5 shared defaults with daily overrides, 6 A, 10 A, 11 A, 12 A. Weather and accessibility remain unsupported. No new rules for rest, density, diversity or reservations were added; existing composition constraints remain in place.

- Meals retain named lunch/dinner slots. New requests use 60 minutes of overlap and a 60-minute category duration. Existing saved meal overrides are preserved.
- Enforce known opening intervals and known closures. Unknown hours may be scheduled and are explicitly marked unverified. Missing closure information does not erase known operating intervals. Existing catalogue hours still need reliable collection: source completeness, last admission, breaks and dated exceptions are not guaranteed by this change.
- Keep directed Kakao driving routes and the persistent demo cache, including endpoint travel. No distance-based fallback. Cache has no expiry and may reflect earlier traffic. Unsupported walking/transit choices are disabled in the form. Provider quotas and cache terms still need operational review.
- Category dwell estimates: cafe 45, meal 60, history/art/entertainment/festival 90, nature/shopping/activity/relax/other 60 minutes. These are initial configurable-in-code assumptions, not measured facts or a team-approved numeric standard. Per-place overrides accept 1–1440 minutes and retain provenance. Both retrieval screening and solver use the same values.
- Independent checkboxes apply the first day's hours or endpoint locations to every day. Uncheck to edit each day independently. Complete entered locations become reusable dropdown choices; saved itinerary endpoint entries are restored.
- No budget input or hard budget enforcement. Display only the known-cost subtotal and count missing visit costs.
- Search the pinned catalogue by name and pin places to a day. Existing locks ensure forced inclusion or a reported failure. No agent needed.
- No travel-minimization phase for application-generated requests. Travel still constrains feasibility. Existing direct optimizer clients retain their previous default objectives.
- Use one standard pace. No new pace controls, weather integration, accessibility feature or reservation agent.

Validation: 161 optimizer/route tests passed. API integration and catalogue tests: 27 passed initially; two old expectations were updated for the new hours policy and both passed on rerun (29 tests total). TypeScript checking and production web build passed. No production DB, paid route call, Docker rebuild/restart or live-browser workflow was exercised.

Resume: rebuild API and optimizer so installed shared Python code includes these changes. Recommender logic is unchanged. The web development service mounts local files. Recheck live planning after connectivity is restored. Previously saved plans keep their original snapshots and display a legacy-hours warning until rebuilt.
