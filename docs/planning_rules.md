# Planning rules

## Times and visits

The form accepts a start/end window and endpoint locations for each day.
Independent options copy the first day's times or locations across the trip.
Complete entered locations can be reused from the location dropdown.

Lunch and dinner use named windows. A meal is required when the requested day
overlaps its window by at least 60 minutes. Day-specific meal overrides take
precedence. See [meal scheduling](dynamic_meals.md).

Visit durations use category estimates: café 45 minutes; meal 60 minutes;
history, art, entertainment, and festival 90 minutes; other activities 60 minutes.
These are configurable assumptions, not measured venue facts. Per-place
overrides accept 1–1440 minutes and record their source. Retrieval and scheduling
use the same values.

Search the pinned catalogue by name to add a must-visit place and select its day.
A locked place is included or planning reports a conflict; it is never silently dropped.

## Hours and routes

Known opening intervals and closures are enforced. Unknown hours may be scheduled
with an unverified-hours warning. A known interval remains binding even if
closure information is incomplete. Breaks and effective dates are preserved by
[normalized schedules](normalized_operating_hours.md).

Driving routes are directed and include the start and return legs. Cached routes
retain their original observation time and have no expiry; they may reflect
earlier traffic. Missing routes stop planning. See [route configuration](../services/planning_data/README.md).

App-generated plans optimize group utility and minimum member relevance, without
a travel-minimization phase. Travel times still constrain feasibility. Direct
optimizer clients retain travel minimization by default.

## Costs and limits

The app does not enforce a budget. It displays the known-cost subtotal and the
number of visits with missing fees. Unknown fees are not marked free.
Walking/transit, weather forecasts, accessibility assistance, reservations, and
adjustable pace are not supported planning features. Chat can request estimated
indoor alternatives or less walking through the existing replanning workflow.

Saved plans retain their captured rules. Rebuild an older plan to apply the
current policy; the itinerary view identifies plans without opening-hours checks.
