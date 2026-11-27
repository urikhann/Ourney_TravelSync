# Conversational confirmation and planning scopes

Follow-up to [validated itinerary repair](localized_itinerary_repair.md), implemented
on `improvements` after commit `1ec3fae`. No deployment, database activation or
production data modification was performed.

## Findings: confirmed code defects versus incident hypotheses

Confirmed from the repository and deterministic regressions:

1. **Confirmation had no durable meaning.** `ask_agent` sent a bare `Yes` back to
   the model. Recent message text was its only link to a preceding question; no
   pending action recorded target, scope, user authorization, version or expiry.
2. **Legacy repair could manufacture locks.** The old approval implementation's
   `keep` attempt put every retained activity into `GenerateInput.locked_items`.
   `_generate` then rendered every optimizer lock as `locked: true`. A stop added
   by subsequent gap filling could remain unlocked, giving the observed shape of
   many locked visits and one flexible visit. This path is reproducible from the
   old source. It is not proof of this particular trip's provenance.
3. **The UI hid application failures.** `AgentBubble` displayed the same generic
   “can't answer right now” sentence for every `FAILED` response, discarding the
   backend's actual explanation. Successful search/hours badges therefore did not
   tell the user whether subsequent routing, solving or E3 had failed.
4. **Gemini ignored its remaining budget.** `GeminiProvider.complete(timeout)`
   accepted a remaining-time argument but used its fixed 20-second client timeout.
   The shared 40-second turn budget includes primary-provider retries, backup
   calls and tool execution. The adapter could start a request longer than the
   remaining allowance after successful read tools.
5. **Protection lacked provenance.** The UI called all locked visits “Reserved”.
   Ordinary approval itself does not write locks: plan voting updates `Vote`;
   proposal application selects/publishes a version. Initial generation should
   mark only requested locks. Completed activities need protection but must not
   be relabeled as user-imposed permanent locks.

The exact historical failure after `@TravelSync Yes.` remains **unconfirmed**.
The trip ID, correlated server logs, saved audit IDs and deployed revision were
not supplied. A usable local Docker CLI/runtime was not available at the standard
Windows path, and no live database investigation was performed. The possible
remaining-budget timeout, a provider failure on the final answer, routing/data
failure, infeasible solve, invalid response or persistence error cannot be selected
as the incident's cause from the badges alone. No production POI facts were invented.

## Execution trace after the read tools

The model service's sequence is `find_places` → `check_hours` → add tool results
to model history → another model request → `answer` → `TripData.validate` → agent
HTTP response. Its hours check certifies neither route connections nor a complete
modified itinerary. `TripData.validate` may also check hours again.

The API then receives the suggestion, resolves its scheduled occurrence, retrieves
operational/routing evidence, applies E1/E2 and localized repair, and runs complete
E3 before storing a draft. Chat serialization and transaction commit happen after
that. The frontend formerly replaced a failure explanation at the last step.

The corrected confirmation path bypasses the model service entirely:

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

Rebuild the API and agent for these changes. If the earlier repair commit has not
been deployed, also rebuild the optimizer. Build the production web image or let
development Vite reload the frontend sources:

```powershell
docker compose up -d --build api agent optimizer
# Production web deployment, only when separately authorized:
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build web
```

## Verification and reproduction

Regression coverage includes English/Korean confirmation, cancellation, missing
context, multiple pending actions, another user's attempted confirmation, expiry,
stale itinerary/constraints, concurrent consumption, owner authorization, confirmation
claim failure, no voting bypass, full/unlocked/day scopes, add/remove, retained legacy
locks, reservations, ordinary approved visits, solver failure after operational
verification and missing final model prose. Driving tests use the real selected-route
repair path with synthetic route responses; day tests preserve the other date's
stored content. API tests use disposable SQLite and the real E2/E3/CP-SAT.

Recorded results (the focused run overlaps the full API run; counts are not additive):

| Check | Actual result |
|---|---|
| Full API rerun | **186 passed, 2 skipped**, 9 warnings; 326.59 s |
| Final affected API modules after the last corrections | **72 passed**, 1 warning; 182.73 s |
| Agent service, including new confirmation/prose/deadline cases | **55 passed**, 1 warning; 2.89 s |
| Root offline suite | **466 passed, 2 skipped, 1 failed**, 21 passing subtests; 72.57 s |
| TypeScript and Vite production build | Passed; final Vite build 3.01 s |
| Staged/working patch whitespace check | `git diff --check` passed, with existing line-ending warnings |

API skips are the opt-in benchmark and live optimizer test. The root failure is
`tests/test_agreed_planning_rules.py::test_travel_objective_can_be_disabled_but_return_still_must_fit`:
its expected objective list omits the existing `waiting_minutes` phase. The same
failure was reproduced from untouched baseline source in the preceding repair
work. It was not hidden by changing constraints or expected solver phases.

The first full API attempt found two regressions: unresolved targets were incorrectly
classified as FAILED, and an older started-activity test compared a dynamic completion
display marker. The state classification was fixed. The test now checks the unchanged
stored itinerary, while completion remains a time-dependent view. The subsequent
full API and final affected-module runs passed.

## Changed files

New implementation modules:

- `api/app/conversation.py`: typed action hints, trusted pending envelopes and atomic confirmation claims.
- `api/app/planning_intents.py`: exact confirmations and distinct planning scopes.
- `api/app/lock_policy.py`: protection provenance and conservative legacy classification.
- `api/app/planning_actions.py`: scoped operations reusing existing repair, solver, E3 and draft persistence.

Updated implementation:

- `api/app/routes/chat.py`, `api/app/config.py`, `.env.example`: action routing,
  group-checked question references, persistence failures, service diagnostics and bounds.
- `api/app/replacements.py`, `api/app/proposals.py`: shared persistence/application
  across scopes, lock provenance, multi-date summaries and existing vote transactions.
- `api/app/drive_planning.py`: optional request transformation for scoped eligibility;
  default generation behavior is unchanged.
- `api/app/routes/itineraries.py`: authoritative protection metadata and explicit-lock labels.
- `services/agent/brain.py`, `services/agent/providers.py`, `services/agent/tools.py`:
  structured questions, missing-prose fallback, remaining-budget handling and authoritative protection context.
- `web/src/pages/Chat.tsx`, `web/src/pages/Plan.tsx`, `web/src/types.ts`, `web/src/i18n.ko.ts`:
  actual failure text, authorized confirmation controls, scoped/multi-date proposals and accurate protection labels.

Tests/documentation:

- Added `api/tests/test_conversation_actions.py`, `api/tests/test_planning_intents.py`,
  `services/agent/test_confirmation_output.py`.
- Updated `api/tests/test_replacement_validation.py` to compare saved content when
  time-dependent completion metadata changes.
- Added this report and linked it from `docs/localized_itinerary_repair.md`,
  `docs/optimizer_app_integration.md`, `services/agent/README.md`.

Concurrent provisional-schedule/data-import work remains separate and was preserved.
No push or deployment was performed; commit history records the local implementation.

```powershell
.venv/Scripts/python.exe -m pytest api/tests -q
.venv/Scripts/python.exe -m pytest tests -q
Push-Location services/agent
../../.venv/Scripts/python.exe -m pytest -q
Pop-Location
```

For manual testing, use a disposable future-dated trip with a published itinerary.
Send `@TravelSync replan from scratch again.` as the trip owner. Without protection,
expect a whole-trip draft or a scoped failure reason. With explicit protection,
expect an unlocked-only confirmation question. Answer via its button, `@TravelSync
Yes.` or `@TravelSync 네`. Confirm that no votes or plan changes occur until a
separate majority approves the validated proposal. Reject/expire/change the plan
during confirmation and verify that the old action cannot run. Use the regression
fixtures for injected optimizer/provider/database failures rather than editing live
operational data.

Remaining limits: search uses the pinned bounded candidate pool and existing route
providers; add/remove use conservative gap reconstruction rather than a new optimizer.
Unrecognized new conditions require clarification. Legacy protection without sufficient
audit evidence remains protected. No live LLM, routing provider, PostgreSQL concurrency
or browser interaction was exercised in this follow-up; UI type/build checks and
deterministic transport fixtures are distinct from live validation. Previous latency
benchmarks describe commit `1ec3fae`, not these new broad planning scopes.
Old free-text questions that predate structured pending state cannot be safely
backfilled from prose; their follow-up requires a fresh explicit request. User-defined
new dates/conditions outside the supported scope grammar require clarification rather
than automatic changes to the trip's hard constraints.
