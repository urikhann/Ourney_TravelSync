# Dynamic meal scheduling (meal-1.0)

Implemented November 14, 2026. Times are integer wall-clock minutes on each
calendar date in Asia/Seoul. No UTC conversion or overnight day windows are
performed here; existing dated-day and operating-data contracts still apply.

## Defaults and calculation

`optimizer/contracts.py::DEFAULT_MEAL_WINDOWS` defines lunch 11:30–14:30
(690–870) and dinner 17:30–20:30 (1050–1230). `DailyComposition` centrally
defines the 45-minute overlap threshold and policy version.

For each requested day and each effective meal window:

```
overlap = max(0, min(day.end_min, meal.end_min)
                 - max(day.start_min, meal.start_min))
required = explicit_requirement or overlap >= meal_overlap_min
```

Requirements use the user's requested day bounds, not a shortened restaurant or
member-availability window. A required meal remains required if routes, duration,
availability or other hard constraints make it impossible. Exactly 45 minutes
requires a meal; 44 does not. A 60-minute stay cannot fit a 45-minute overlap.

Examples: 09:00–21:00 requires both meals; 09:00–14:00 lunch;
14:00–21:00 dinner; 14:30–17:30 neither. Requirements are independent per date.
Other composition rules (activities, variety, café cap, food/drink separation)
still apply, so a short day can remain infeasible for reasons besides meals.
The existing nonempty-day rule remains in place.

Omitting `PlanRequest.daily_composition` now materializes the default policy in
the input snapshot. Explicit `null` retains the legacy no-composition mode for
standalone callers. The authenticated app always supplies a composition policy.
The old `min_meals` field is accepted but deprecated and ignored: it cannot force
two meals on a morning-only day. `max_meals` remains enforced. Breakfast is no
longer a default slot but remains supported in custom base configurations.

## CP-SAT and E3

`solver.solve_prepared` creates a Boolean assignment for each selected meal POI,
date and effective slot. The sum across slots equals the visit Boolean, so one
scheduled visit occupies exactly one slot. Each slot holds at most one visit;
required slots hold exactly one. Start and full duration must fit the slot;
dated E2 opening/member windows and route propagation also constrain the same
start variable. Day bounds and return-leg constraints remain unchanged. Cafés
cannot satisfy meals: the existing `planning_categories.visit_role` classifier
gives café precedence, then classifies the imported food feature as a meal.
No new restaurant suitability metadata is inferred.

Optional meal visits can be selected if they improve the existing objectives;
there is no extra meal reward. The existing cross-day no-repeat rule means two
required meals need distinct POIs, and multi-day trips need enough distinct POIs.
Existing full-catalogue retrieval already reserves restaurant alternatives; it
was not changed, nor were GFAR or individual/group utility calculations.

E3 reconstructs meal assignment from actual visit start/departure times and the
input policy, using an exhaustive injective matching over the small meal-slot
set. It does not trust solver assignments, selected-POI counts or E2 eligibility
flags. It checks fixed starts, complete windows, required-slot coverage and
distinct visits even when overridden windows overlap. Existing validation still
checks duration, travel, endpoints, exclusions, availability and enabled cost/
operating constraints.

Preferred starts add an optional fourth lexicographic phase: minimize absolute
start deviation after pinning the existing group utility, minimum member
relevance and travel objectives. Search allocations are 70%/20%/5%/5% with a
preference, otherwise the existing 70%/20%/10%. A preferred time is soft and may
be missed when hard constraints or earlier objectives take priority, or search
times out. Fixed starts are hard. Neither preference changes scores or makes a
failed hard constraint optional. Response phases expose deviation and status.

## Structured overrides and future agent boundary

`MealOverride` takes `date`, `name`, optional `window` (`start_min`, `end_min`),
either `preferred_start_min` or `fixed_start_min`, and optional `required`.
Only the specified date and meal change. A time request is not automatically a
window request. Supplying a start explicitly requires that meal, even below the
overlap threshold. Supplying a window alone recalculates the overlap rule;
`required: true` preserves an explicit meal requirement.

Without an explicit window, an in-window target retains the base window. An
out-of-window target translates that meal's window to begin at the target and
retain the base width (clipped at 1440). Thus fixed lunch at 15:00 produces an
effective 15:00–18:00 window with start exactly 15:00. An explicit window takes
precedence; a target outside it is rejected. Unknown dates, duplicate day/meal
overrides, simultaneous fixed/preferred starts and starts outside the travel day
are rejected. A valid target with insufficient time for the full stay is passed
to optimization and returns infeasibility, without fallback to defaults.

Backend callers can use the pure function:

```python
updated = update_meal_constraints(
    request,
    MealOverride(date=travel_date, name="lunch", fixed_start_min=900),
    constraint_version="new-version",
)
result = optimize(updated)
```

It returns a revalidated `PlanRequest`, requires a changed constraint version,
and never mutates the input or a saved plan. The effective inputs (base windows,
overrides, threshold and policy version) are fully included in the replay
snapshot. The app fingerprints the full generation configuration for its
constraint version, using existing JSON audit columns; no migration is needed.

The existing chat is a mock, without a tool executor. A future tool can call:

```
POST /trips/{trip_id}/itineraries/meal-proposals
Authorization: existing trip-owner bearer token
{
  "configuration": { ...existing GenerateInput... },
  "change": {
    "date": "2026-11-06",
    "name": "lunch",
    "fixed_start_min": 900
  }
}
```

The endpoint reuses catalogue retrieval → operational snapshot → CP-SAT → local
E3 and existing stale-input guards. It returns `proposal_id`,
`status: AWAITING_APPROVAL`, updated `configuration`, `constraint_version`,
scheduled `days`, `metrics`, `validation_status` and `enforcement` flags.
The private audit is `PREVIEWED` with no attached itinerary. Successful and
failed proposals leave every saved itinerary and vote untouched. Solver
infeasibility returns HTTP 409 with a code and required-meal conflicts; malformed
overrides return 422. Reported unscheduled meals identify requirements in the
failed model; they are not a minimal explanation of the root cause.

There is no real LLM parser, approval controller or commit-proposal operation in
this change. The future chat controller must distinguish preferred versus fixed
intent, resolve phrases such as “tomorrow” to a Seoul date, collect required group
approval, and recheck proposal/input freshness before committing a revision.
Do not route an unapproved agent proposal through ordinary `/generate`, whose
existing owner-triggered rebuild behavior remains unchanged.

## Data and verification limits

Per the requested app configuration, **budget and opening-hours enforcement
remain disabled** in both ordinary app generation and meal proposals. E2/CP-SAT/
E3 respect those request flags consistently. Standalone requests retain strict
defaults, and tests exercise enabled hours/budget enforcement. An app E3 pass
does not establish real restaurant availability or affordability. Unknown POI
fees remain excluded from accounted totals under the existing cost policy.
No operational data was created or changed.

Validation uses synthetic facts/routes and isolated temporary SQLite databases,
without live external API calls. Tests cover day windows and exact boundaries,
duration/closing/end/travel conflicts, nonmeal roles, distinct visits, candidate
shortage, overrides, snapshot isolation, E3 tampering, owner authorization and
saved-plan/vote preservation. See `test_optimizer_meals.py` and
`api/tests/test_meal_proposals.py`.

Modified implementation entry points: `contracts.py` (policy/data contracts),
`meals.py` (effective windows, updates and independent matching), `solver.py`
(assignments/preference phase), `validation.py` (E3), optimizer `api.py`
(infeasibility diagnostics), app `optimization.py` (input validation), app
`routes/itineraries.py` (preview workflow and default policy response). The Plan
form/types now describe server-supplied defaults instead of hardcoded old slots.
Legacy optimizer fixtures/examples explicitly opt out of composition; existing
composition and candidate tests use the new defaults.

## Verification results

Final local checks:

- All `tests/test_optimizer_*.py`: **180 passed**, no failures or skips.
- API meal proposals, optimizer integration and planning candidates:
  **32 passed**, no failures or skips.
- TypeScript compiler API, using the existing `web/tsconfig.json`: **0 diagnostics**.
- `git diff --check`: passed.

Both Python suites report the installed Starlette/httpx TestClient deprecation
warning. The initial API test run was blocked by missing dependencies; the
existing `api/requirements.txt` dependencies were installed into `.venv`, then
the suites passed. Two initial new assertions incorrectly prohibited optional
objective-improving restaurant visits; they were corrected to check required
coverage. The legacy documented example now explicitly opts out of composition.
No final test failures remain. A production Vite bundle was not built because
`npm` is unavailable on PATH; TypeScript validation used the installed compiler
through the Node runtime. Live operational data, Docker and PostgreSQL were not
tested by these checks.

Complete changed-file inventory:

| File | Main change |
|---|---|
| `src/ourney_travelsync/optimizer/contracts.py` | `MealOverride`, central defaults, versioned policy, request validation |
| `src/ourney_travelsync/optimizer/meals.py` | `effective_meals`, `validate_meal_overrides`, `update_meal_constraints`, `meals_can_be_assigned` |
| `src/ourney_travelsync/optimizer/solver.py` | `solve_prepared`: per-meal assignments and final preference phase |
| `src/ourney_travelsync/optimizer/validation.py` | `validate_final`: independent actual-visit meal matching |
| `src/ourney_travelsync/optimizer/api.py` | `optimize`: failed-model meal diagnostics |
| `api/app/optimization.py` | `GenerateInput.valid_meals`: override preflight |
| `api/app/routes/itineraries.py` | `propose_meal_change`, shared `_generate`, server-supplied policy defaults |
| `web/src/components/OptimizationForm.tsx` | Display dynamic policy from the server |
| `web/src/types.ts` | Override/default-policy response types |
| `tests/test_optimizer_meals.py` | New policy, CP-SAT and E3 tests |
| `tests/test_optimizer_composition.py` | Two default meal slots |
| `tests/optimizer_fixtures.py` | Explicit legacy no-composition fixture |
| `api/tests/test_meal_proposals.py` | Preview, conflicts, authorization, plan/vote preservation |
| `api/tests/test_planning_candidates.py` | Dynamic-minimum expectations |
| `contracts/optimizer.synthetic.request.json` | Explicit legacy no-composition example |
| `contracts/optimizer.synthetic.response.json` | Matching example input snapshot |
| `docs/dynamic_meals.md` | Policy, interface, limitations and verification report |
| `docs/optimizer.md` | Link to this policy update |
