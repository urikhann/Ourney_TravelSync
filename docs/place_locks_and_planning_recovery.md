# Place locks and planning recovery

The Plan timeline dot is an owner-operated lock button. Filled dots show a locked
place; outlined dots show an unlocked place. Both have lock icons, keyboard focus,
a 44px touch area and English/Korean instructions. A tap persists protection
metadata immediately. Reservations and activities already started remain protected.
Members can see the state but cannot edit it.

The authenticated endpoint checks trip membership, ownership, saved-plan publication
and a lock revision. Conflicting edits return a refresh message. Lock edits retain
the saved visit order, times, routes, historical optimizer request and group votes.
The changed plan fingerprint makes preceding confirmation/proposal snapshots stale.
An audit trail records the editing user, timestamp and requested protection state.
Temporary retention locks remain distinguished from explicit user protection.

## Confirmed LALALA findings

Read-only inspection found nine mandatory places in the failed request, including
three cafes, against a hard maximum of two cafes. This is infeasible regardless of
route availability. An older applied repair had promoted retained visits into locks;
the form subsequently reused the latest failed attempt's configuration, including an
additional cafe. The new form defaults take protection from the selected published
plan, while retaining other recent settings. Failed/private additions remain in the
diagnostic record and no longer become default must-visit choices.

A replay using provisional geography was infeasible with the captured nine locks
and feasible after removing the added cafe while retaining the cafe cap. This is
evidence of the count conflict, not proof of a road-route-verified replacement plan.
No live itinerary or database settings were changed during investigation.

Preflight now rejects mandatory cafe/meal counts exceeding daily maxima with the
specific `optimizer_lock_composition_conflict` reason before external work. Incoming
user locks and hard constraints are preserved. Initial driving draft failures now
persist their request, response and stage instead of leaving an empty replay record.
Earlier timeout failures remain separate; the count conflict does not establish
their cause. A later live database recheck was unavailable, so no post-change live
generation or provider verification is claimed.

## Local verification

Regression tests cover owner lock/unlock persistence, unchanged schedule/votes and
historical replay, stale edit rejection, member authorization, reservation/completed
protection, failed-attempt form recovery, preserved initial explicit input, impossible
lock-count preflight and persisted driving draft failure diagnostics. Tests use
disposable SQLite databases and synthetic planning evidence. Frontend TypeScript
and Vite builds verify integration; touch behavior has not been exercised on a device.

Verification includes the lock-toggle and temporary-retention regressions. The
final issue audit passed the root/API suites, TypeScript, Vite and whitespace
checks; see issues_16_29_31_audit.md for counts and limitations.
