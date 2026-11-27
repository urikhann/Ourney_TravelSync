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

## Form recovery and conflicts

Must-visit defaults come from the selected published plan's protection state.
Failed or private attempts do not become default must-visit choices. Other recent
form settings remain available. Mandatory cafe/meal counts exceeding daily maxima
return `optimizer_lock_composition_conflict` before provider calls. Initial driving
draft failures retain request, response and stage in the private audit.

See [test commands](optimizer_verification.md) for lock persistence, authorization,
stale edits, reservations, completed visits and form-recovery regressions.
