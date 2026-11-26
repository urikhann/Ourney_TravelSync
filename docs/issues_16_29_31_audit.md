# Issues 16 and 29–31: improvements branch audit

Requirements were checked against the current GitHub issues and issue 16's
breakfast comment on 26 November 2026. Implementation is complete within the
agreed MVP scope below. This is a local branch assessment, not issue closure,
remote CI success or a claim of exhaustive live-provider validation.

| Issue | Implemented behavior | Evidence |
|---|---|---|
| [16](https://github.com/urikhann/Ourney_TravelSync/issues/16) | Optional evening toggle automatically chooses a fitting bar/pub after 18:00; at most one automatic stop per day, with explicit older locks preserved. Alcohol/place exclusions, availability, durations, routes, return travel and E3 still apply. Stops remain separate from dinner/activity minima. Optional breakfast supports inferred/verified suitability, strict verification mode and omission when infeasible. Cafes fitting the full breakfast window receive the requested timeline label. | `tests/test_evening_choices.py`, `tests/test_evening_retrieval.py`, `tests/test_optimizer_breakfast.py`, `tests/test_breakfast_retrieval.py`, `api/tests/test_automatic_evening.py`, `api/tests/test_breakfast_planning.py`; `OptimizationForm.tsx`, `planLabels.ts` |
| [29](https://github.com/urikhann/Ourney_TravelSync/issues/29) | Selected POI-to-POI edges determine waiting; departure/return legs are excluded. Waiting minimization follows existing prioritized objectives, retaining earlier incumbents on timeout. Required meals and intentional breaks remain constraints. Waiting totals and per-gap accounting survive publication. | `tests/test_optimizer_waiting.py`, `tests/test_agreed_planning_rules.py`, `api/tests/test_waiting.py`; optimizer `solver.py`, `validation.py`, `waiting.py` |
| [30](https://github.com/urikhann/Ourney_TravelSync/issues/30) | Configurable buffer/rest/fill/reoptimize thresholds. Normal driving shortlists up to three unused preference-ranked activities, obtains missing directed road legs, runs a bounded repair, and independently validates before accepting improvement. Preserves fixed activities and the incumbent on routing/quota/timeout/invalid-output/infeasibility failures. Duplicate source IDs and recognized physical-place aliases are excluded. | `api/tests/test_driving_gap_filling.py`, `tests/test_optimizer_waiting.py`, `tests/test_place_identity.py`; `drive_planning.py`, optimizer `api.py` and `waiting.py` |
| [31](https://github.com/urikhann/Ourney_TravelSync/issues/31) | Dedicated timeline gap rows show start, end and duration using backend arrival/start values. Labels cover free/rest time, short travel buffers, explicit fixed meals and enforced known opening boundaries. Generic labels remain when a cause is unsupported. Generation and approved modifications recompute gaps. | `WaitingPeriod.tsx`, `Plan.tsx`, `api/tests/test_waiting.py`, `api/tests/test_removal_repair.py`; replacement publication uses fresh E3 gap metrics |

## Gaps corrected during this audit

- Gap-repair acceptance now treats `optional_stops_and_group_utility` as a
  maximization objective, preserving the direction of optional evening/breakfast
  priorities when comparing an improved schedule.
- The disabled-travel-objective test now expects the final waiting objective
  required by issue 29; return-trip feasibility checks remain in place.
- Opening-time and travel-buffer labels now use supported backend reason codes.
  Unknown/estimated hours do not justify an opening-time explanation. Explicit
  fixed breakfast at a cafe can receive the breakfast waiting label.

## Cumulative changes included with this audit

The commit also includes the already completed place-lock toggle and permissions,
failed-attempt form-default recovery, explicit lock provenance, physical-place
identity guards, removal repair, POI proposal detail links pinned to the saved
dataset, breakfast labels and requested timeline visibility changes, and chat
composer locking/immediate clearing while answers load. Existing group approval,
hard validation and saved-version safeguards remain active.

The previously authorized V5 provisional-schedule amendment and its packaged
manifest/specification, reader integration, import script and regressions are
included. Estimated/unresolved schedules remain distinct from confirmed hours.
Committing these files does not rerun import or activation. Private DB backups,
local replays, caches and credentials are excluded from Git.

## Verification

- Root suite: **493 passed, 2 skipped**, plus 21 passing subtests.
- Full API suite: **210 passed, 2 skipped**.
- Recommender service: **18 passed**.
- Agent service: **55 passed**.
- Final waiting/constraint regressions: **24 passed**.
- TypeScript and Vite production build passed.
- Wheel build passed; new identity/provisional modules and the V5 manifest are
  present in the wheel.
- V4 and amended V5 packaged deployment hashes match their specifications.
- Changed-file credential-pattern scan and whitespace checks passed.

## Scope and limits

- Budget enforcement remains disabled for this MVP, as explicitly requested.
- Gap filling is bounded to the existing planning pool; it does not guarantee
  discovery across every catalogue POI. Pace-based automatic threshold selection
  is optional in the issue and is not implemented; thresholds/breaks can be set
  through the API and are preserved by the form.
- Break/threshold controls are not separate visible form controls. Opening labels
  describe a known enforced boundary; they do not independently verify fresh hours.
- Tests use controlled routing/operating evidence. The captured LALALA removal
  replay passed E3 using cached road routes, without new provider requests or a
  live itinerary change. This does not establish every live provider/quota case.
- Local dependency auditing found an incomplete optional `orjson` installation
  (missing package initializer/distribution metadata) in the shared virtualenv.
  No dependency requirement was changed. A fresh CI environment was not run here;
  local test/build success is not a claim of remote CI success.
