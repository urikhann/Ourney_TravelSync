# Application guide

## Plan a group trip

1. Register, verify your email, and sign in.
2. Create a trip and invite the other members. Trips support up to ten members
   and one to three days.
3. Each member enters ten preference ratings from 1 to 5, personal exclusions,
   and confirmation for every trip date. Optional taste selections provide text
   for recommendation methods that use it.
4. Generate group recommendations. Place details include available descriptions,
   reported hours, prices, and map links. Missing information stays unknown.
5. Enter daily start/end times and locations. You can reuse the first day's
   settings, pin must-visit places, and override estimated visit durations.
6. Build the itinerary. The planner checks routes, day bounds, known opening
   windows, member rules, and daily composition. A failed rebuild keeps the saved
   itinerary and its votes.
7. Review the stops, unknown-hours warnings, cost subtotal, and solver status,
   then vote on the saved plan.

## How the components work

| Component | Behavior | Reference |
|---|---|---|
| Accounts and trips | Token authentication, membership checks, invitations and versioned preferences | `api/app/routes/` |
| Individual scoring | E5 hybrid relevance with labeled feature fallback | [Scoring](e5_hybrid.md) |
| Group recommendations | Per-member candidate union and GFAR aggregation | [Tie handling](tie_policy.md) |
| Itinerary candidates | Day-specific retrieval from the pinned catalogue | [Candidate retrieval](itinerary_candidate_retrieval.md) |
| Planning data | Operating facts and directed Kakao driving routes | [Planning service](../services/planning_data/README.md) |
| Optimization | CP-SAT scheduling followed by independent E3 validation | [Optimizer](optimizer.md) |
| Publication | Fresh-input checks and atomic replacement of saved plans | [API integration](optimizer_app_integration.md) |
| Chat assistant | Read-only tools and validated suggestions, with a rules fallback | [Agent](../services/agent/README.md) |

## Limits

- Availability entry confirms dates within the shared daily window; it does not
  collect each member's hourly schedule.
- Driving is the supported route mode. Missing route data stops planning.
- Known hours and closures are enforced; unknown hours require confirmation.
- Visit durations are estimates unless overridden. Budget limits, weather, and
  accessibility assistance are not exposed as supported planning features.
- Costs show only supplied fees and fares. Hybrid relevance and GFAR gains are
  not measured satisfaction or acceptance probabilities.
- Email delivery requires SMTP configuration. Without it, codes appear in local logs.

See [Docker setup](../DOCKER.md) for service configuration and
[optimizer tests](optimizer_verification.md) for validation commands.
