# Reviewing proposed places

Chat proposals include expandable cards for original, proposed and rescheduled
places. Each card shows the category and exact visit time before expansion.
Expanded cards show the photo, description, address/map, visit duration, saved
opening-hours checks, estimated price per person, group recommendation score and
available recommendation reasons. Missing facts are labeled explicitly; missing
prices do not mean free admission. English and Korean labels are available.

The impact panel compares travel time (including the return journey) and elapsed
day duration in the original and proposed schedules. Cards use native disclosure
controls, so keyboard users can expand them. “More place information” opens the
existing shared POI sheet with the proposal's pinned dataset version.

## Source and approval boundaries

The API derives `proposal_details` from the proposal's private validated draft and
its bound original optimization run, both within the current trip. It checks the
declared additions/removals against those snapshots and batch-loads public POI
metadata from the pinned dataset version. It does not accept detail fields from
LLM output. Member identifiers and individual preference scores are not exposed.
Public links are limited to HTTP(S) and omit credential-bearing provider URLs.

Hours, visit times, recommendation scores and planning estimates come from saved
optimizer inputs/results, rather than another provider call when reading chat.
Static POI metadata supplies descriptions, photos and addresses. Relative ranking
scores are labeled as relative relevance, not satisfaction probabilities. Missing
recommendation reasons remain unavailable. Price information does not enable a
budget constraint.

Opening a card or POI sheet neither casts a vote nor applies a change. Existing
group approval thresholds, explicit locks, reservations and final constraint
validation remain unchanged. Older or incomplete proposal snapshots retain the
previous place-detail links and display a snapshot-unavailable label. The shared
POI sheet can make its existing live availability request; its result does not
replace the saved proposal facts shown inline.

## Validation

API regressions cover snapshot accuracy, travel/day-duration deltas, missing
metadata, score semantics, legacy records, cross-trip isolation, rejection of
fabricated detail fields, safe links and unchanged voting/publication behavior.
Frontend checks cover the production TypeScript/Vite build and actual component
rendering in English/Korean, including missing facts and adjusted visit times.
These checks do not establish live provider availability or physical-device UI
behavior.
