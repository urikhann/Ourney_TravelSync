# Conversational confirmation and planning scopes

An unchanged planning result is informational: no voting draft is stored and the
reply explains that the search returned the same itinerary. The comparison uses
places, visit order, schedule and travel details rather than aggregate totals or
evidence metadata. Existing pending drafts with identical original and proposed
schedules also display this explanation and cannot accept votes.

## Weather and walking shortcuts

The rain and less-walking shortcuts request `replan_day` directly, without a model
call or a single-place replacement. “Today” is resolved in Asia/Seoul; when that
date is absent from the published itinerary, the response asks for a date.
The existing owner permission for day replanning applies.

Rain puts estimated outdoor visit minutes first in the solver's objective order.
Saved indoor metadata identifies sheltered visits; unknown indoor status is
conservatively counted as outdoor. Less walking puts estimated visit walking
burden (duration times saved walking level) plus route walking minutes first.
Unknown walking levels use the highest burden. These metadata estimates do not
certify indoor conditions or measured walking within a place.

All editable visits on the date can change. Protected visits, other dates, meal
requirements, availability and final validation remain enforced. The bounded
search must produce a strictly lower day burden before a draft is offered;
otherwise the current itinerary remains unchanged. A validated draft still needs
the existing group vote. The request is a minimization objective, not a guarantee
of a globally optimal result within the search and routing budgets.

See [validated itinerary repair](localized_itinerary_repair.md).

## Execution trace after the read tools

The model service's sequence is `find_places` → `check_hours` → add tool results
to model history → another model request → `answer` → `TripData.validate` → agent
HTTP response. Its hours check certifies neither route connections nor a complete
modified itinerary. `TripData.validate` may also check hours again.

The API then receives the suggestion, resolves its scheduled occurrence, retrieves
operational/routing evidence, applies E1/E2 and localized repair, and runs complete
E3 before storing a draft. Chat serialization and transaction commit happen after
that.

The confirmation path bypasses the model service entirely:

```text
Persisted question + explicit affirmative / negative
  → group, user, question, permissions, version and expiry checks
  → atomically claim the pending operation once
  → the existing deterministic action / repair pipeline
  → full E3 and private validated draft
  → deterministic response and persisted chat reply
  → a separate group vote
  → existing atomic approval / exact-draft application
```

There is no final LLM request after a backend draft exists. Missing model prose
cannot discard a valid candidate: the model service provides a deterministic
handoff sentence, and the API independently validates and templates the actual
result. Invalid tool-summary shapes are filtered before serialization. Backend
failures retain their structured reasons; the frontend displays those explanations.
An unstructured model confirmation question is rejected in favor of clarification,
so the next `Yes` cannot silently authorize a guessed operation.

## Pending action storage and authorization

The existing agent `ChatMessage.agent` JSON stores `pending_action`, containing:

- action ID/type and canonical operation, scheduled target item ID when applicable;
- group ID, authorized user ID, permission/role snapshot;
- itinerary ID, optimizer-run version and full itinerary hash;
- extracted constraints and a fingerprint of current group/constraint state;
- creation/expiration timestamps, originating user message and agent-question IDs;
- confirmation status, confirming message ID/time, resulting draft or failure reason.

The model can supply a typed operation hint, never this trusted envelope. The
backend validates operation fields, resolves the actual target, creates permissions
from membership and pins the current version. Unsupported new conditions require
clarification; they are not ignored. Day and whole-trip re-planning preserve the
existing owner-only planning authorization. Individual proposals remain available
to members and require the existing group vote before publication.

`Yes`, `Yes please`, `OK`, `네`, `예`, `응`, `좋아요` and other exact affirmative
forms are deterministic. Exact negatives decline the operation. Qualified or
ambiguous responses do not consume it. An explicit `reply_to` identifies a question
when several are pending; the API checks that the referenced agent message belongs
to the same group. Buttons are shown only to the authorized user. A plain response
cannot jump past a different intervening question to the same user.

The trip and question are locked while checking/claiming. SQLite additionally
acquires a write reservation. The claim is committed **before** external planning,
preventing duplicate execution under concurrent confirmations. Question states are
`PENDING` → `CONFIRMED` → `CONSUMED`, or `DECLINED`, `EXPIRED`, `STALE`.
Expired, stale, missing, ambiguous and already-handled actions return specific
reasons. A crash after the claim may leave `CONFIRMED`; it cannot automatically
execute again, and the user must make a fresh request.

Confirmation starts a search or draft operation. It creates **zero proposal votes**.
The confirmation message ID cannot be used as a votable proposal. The subsequent
validated reply has its own proposal ID and group-voting controls.

## Scope routing

| Intent | Behavior |
|---|---|
| `replace_poi` | Resolves the exact occurrence and calls the existing localized replacement pipeline, including for confirmed actions. |
| `remove_poi` | Removes only the resolved unprotected occurrence, reconstructs its adjacent edge through existing helpers, and requires whole-itinerary E3. Mandatory composition is not relaxed. |
| `add_poi` | Resolves a named candidate from the pinned pool, verifies its facts/routes and tries existing gaps while retaining current visits/times. Unknown hours cannot qualify. |
| `replan_day` | Solves only the named date; excludes visits retained on other dates and merges other dates unchanged before E3. |
| `replan_unlocked` | Reuses the existing optimizer and selected-route driving adapter while pinning explicit locks, reservations and completed visits at their actual times. |
| `replan_all` | Re-solves all dates when no protected visits conflict. Otherwise explains the conflict and records an explicit unlocked-only alternative for confirmation. It never silently becomes a single swap. |

When all visits are protected, the backend explains that protected changes require
separate explicit authorization in planning settings. Chat confirmation cannot
unlock activities or cancel reservations. No new unlock endpoint was introduced.
Newly selected POIs require positively verified dated hours. Retained visits follow
the app's existing unknown-hours policy. Scoring, GFAR, explicit exclusions, date
availability, route limits, meal/composition rules and the app's disabled budget
policy remain unchanged.

All scopes share `replacements.persist_draft` and `apply_draft`. Versions, snapshots,
E3, fresh approval evidence, rollback and majority voting therefore remain common.
Scope/date summaries and actual new visit times are shown before voting. Applied
re-planning versions do not acquire permanent locks merely because members approved.

## Lock provenance and legacy safety

`lock_policy.describe` distinguishes explicit locks, confirmed reservations,
completed/started visits, ordinary visits and unexplained legacy flags. New
generation/application records protection reasons separately from approval.

Legacy retention is recognized only when a saved APPLIED chat record points to the
child audit, its parent audit exists, and the child's complete lock configuration
exactly matches the legacy `keep`/`rebuild` construction. Comparison normalizes
nullable `start_min` from actual serialized configurations. Parent explicit locks
are preserved through the lineage. Reservations and explicit per-item markers stay
protected. If proof is incomplete, legacy flags remain protected; no lock is guessed
away. Classification does not rewrite historical itinerary rows.

## Configuration, deployment and observability

No migration or new dependency is required. Existing JSON/audit tables are reused.

| Setting | Default | Meaning |
|---|---:|---|
| `PENDING_ACTION_VALID_SECONDS` | 300 | Lifetime of a search/re-planning confirmation question |
| `PLANNING_ACTION_SECONDS` | 60 | Total deadline for add/remove or broader planning operations |

Single replacement searches still use their existing 20-second default; approval
still revalidates the stored schedule without another solve. Gemini requests use
`min(20 seconds, remaining turn budget)` with one SDK attempt. No retry count or
timeout limit was increased. Total action, solver-search, external optimizer,
planning-data and LLM timeouts remain distinct diagnostic conditions.

Logs correlate action/message ID, trip, scope, stage, draft ID, E3 errors and safe
exception type. Failed scoped optimization attempts retain private audit status and
reason. Provider payloads, SQL parameters and preference vectors are not logged.
Exact historical provider timing cannot be reconstructed from old generic badges.

Rebuild API, agent and optimizer together when updating shared planning code.
For a production frontend, rebuild its image too:

```powershell
docker compose up -d --build api agent optimizer
# Production web deployment, only when separately authorized:
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build web
```

## Verification and limits

Regression tests cover English/Korean confirmation, cancellation, authorization,
expiry, stale inputs, concurrent consumption, planning scopes, protected visits,
rollback and provider failure. See [test commands](optimizer_verification.md).

Search uses the pinned bounded candidate pool and configured route providers.
Unknown conditions require clarification. Legacy protection without sufficient
audit evidence remains protected. Old free-text questions without structured
pending state require a fresh explicit request. Confirmation prepares a draft;
a separate group vote is required to apply it.
