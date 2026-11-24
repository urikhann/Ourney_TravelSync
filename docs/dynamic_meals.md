# Dynamic meal scheduling (meal-1.0)

Times are integer wall-clock minutes on each
calendar date in Asia/Seoul. No UTC conversion or overnight day windows are
performed here; existing dated-day and operating-data contracts still apply.

## Defaults and calculation

`optimizer/contracts.py::DEFAULT_MEAL_WINDOWS` defines lunch 11:30–14:30
(690–870) and dinner 17:30–20:30 (1050–1230). `DailyComposition` centrally
defines the standalone 45-minute overlap threshold and policy version.
The application supplies a 60-minute threshold.

For each requested day and each effective lunch or dinner window:

```
overlap = max(0, min(day.end_min, meal.end_min)
                 - max(day.start_min, meal.start_min))
required = explicit_requirement or overlap >= meal_overlap_min
```

Requirements use the user's requested day bounds, not a shortened restaurant or
member-availability window. A required meal remains required if routes, duration,
availability or other hard constraints make it impossible. At the standalone default, exactly 45 minutes
requires a meal; 44 does not. In the app, the boundary is 60 minutes. A 60-minute stay cannot fit a 45-minute overlap.

Examples: 09:00–21:00 requires both meals; 09:00–14:00 lunch;
14:00–21:00 dinner; 14:30–17:30 neither. Requirements are independent per date.
Other composition rules (activities, variety, café cap, food/drink separation)
still apply, so a short day can remain infeasible for reasons besides meals.
The existing nonempty-day rule remains in place.

Omitting `PlanRequest.daily_composition` materializes the default policy in
the input snapshot. Explicit `null` retains the legacy no-composition mode for
standalone callers. The authenticated app always supplies a composition policy.
The old `min_meals` field is accepted but deprecated and ignored: it cannot force
two meals on a morning-only day. `max_meals` remains enforced. Setting
`breakfast_enabled: true` adds breakfast 07:00–10:00 (420–600); the default
remains false for compatibility with saved lunch/dinner configurations.
An explicitly configured breakfast window also enables that slot. By default
the solver maximizes the number of days with breakfast before group utility.
It skips breakfast when needed to satisfy the hard constraints. Breakfast
time overrides constrain a breakfast visit if selected.
Set `breakfast_require_verified: true` to use strict mode. In strict mode an
overlapping breakfast window or explicit breakfast requirement makes the meal
mandatory and requires verified offering, serving and operating evidence.

## CP-SAT and E3

`solver.solve_prepared` creates a Boolean assignment for each selected meal POI,
date and effective slot. The sum across slots equals the visit Boolean, so one
scheduled visit occupies exactly one slot. Each slot holds at most one visit;
required slots hold exactly one. Start and full duration must fit the slot;
dated E2 opening/member windows and route propagation also constrain the same
start variable. Day bounds and return-leg constraints remain unchanged. Ordinary
cafés cannot satisfy meals. A breakfast-appropriate café can cover breakfast
only, while retaining its café role and café cap. It also consumes one meal slot
and counts toward `max_meals`; it can still be selected as an ordinary café at
other times. Lunch and dinner continue to require restaurant meal roles.

## Breakfast evidence and data gaps

Versioned `Poi.enriched_metadata` can contain `breakfast_offering`, a standard
`Fact[bool]` confirming a breakfast menu, or `breakfast_category`, a `Fact[str]`
with value `breakfast_restaurant`, `breakfast_cafe` or `brunch_cafe`. Both require
`status: KNOWN`, a source and verification timestamp. Explicit imported
`representative_menu` or `detailed_category` text mentioning breakfast/brunch
is also accepted only when its source URL and checked timestamp match the
field's entry in `field_provenance_json`. Names, descriptions, generic food/café
tags and individual foods such as toast are insufficient. An explicit negative
breakfast fact takes precedence over older imported text.

`breakfast_hours` maps ISO calendar dates to `Fact[tuple[Window, ...]]` serving
intervals in local minutes. Known intervals restrict the full stay in either
mode. A verified empty interval list excludes breakfast that day. Optional mode
can infer suitability from explicit breakfast or brunch cues in available menu
or category fields, including fields without verification provenance. The full
visit must still fit a known opening interval and the breakfast slot, and a known
closure disqualifies the stop. Opening hours establish when the venue is open.
Breakfast is labeled `verified` only when the strict evidence check confirms
the full visit, including known `closed: false`. Otherwise the visit is labeled
`inferred` and the UI asks the traveler to confirm serving times. E3 checks the
recorded assignment against the pinned optimizer input.

Synthetic metadata example (not a real venue fact):

```json
{
  "breakfast_offering": {
    "value": true, "status": "KNOWN", "source": "synthetic fixture",
    "verified_at": "2026-11-01T00:00:00Z"
  },
  "breakfast_hours": {
    "2026-11-06": {
      "value": [{"start_min": 480, "end_min": 600}],
      "status": "KNOWN", "source": "synthetic fixture",
      "verified_at": "2026-11-01T00:00:00Z"
    }
  }
}
```

The pinned V4 catalogue has some sourced brunch-menu text and lacks structured
breakfast-serving hours. Its qualifying stops can be used as inferred breakfast
options when known opening intervals and other hard constraints permit.

Optional breakfast retrieval adds up to three options to the ordinary day
shortlist where the overall 60-place limit allows. Strict mode reserves
verified alternatives within the ordinary shortlist. The planning adapter and
optimizer evaluate opening hours and directed-route feasibility on the selected
candidates. E1 exclusions and GFAR scoring still apply.

Other optional meal visits can be selected if they improve the existing objectives.
The existing cross-day no-repeat rule means two
required meals need distinct POIs, and multi-day trips need enough distinct POIs.
Full-catalogue retrieval reserves restaurant alternatives. Meal constraints do
not modify GFAR or individual/group utility calculations.

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

## Structured overrides and previews

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

The trip owner can request a preview with:

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

Meal proposals return previews only; they do not apply an approved revision.
A controller applying a proposal must obtain approval and check input freshness.
Ordinary owner-triggered `/generate` requests rebuild the saved itinerary.

## Constraints and tests

App generation and meal previews enforce known hours and closures while allowing
unknown hours with warnings. Budget enforcement is disabled. Standalone clients
retain strict defaults. Costs include supplied fees only, so a validated plan
does not establish affordability or complete restaurant availability.

Synthetic tests cover overlap boundaries, full-stay and route conflicts, distinct
meal visits, overrides, E3 tampering, authorization, and saved-plan/vote preservation.
See `tests/test_optimizer_meals.py` and `api/tests/test_meal_proposals.py`.
