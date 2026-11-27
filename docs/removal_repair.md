# Removing an activity between food/drink stops

Direct removal remains the first attempt. If its only E3 failure is food/drink
separation, the existing scoped optimizer and driving verification pipeline now
attempt one affected-date repair. The candidate set contains only the remaining
visits, and solver-only retention constraints require every one of them. Genuine
locks, reservations and completed activities retain their protected times. Original
times are soft preferences for movable visits; other dates merge unchanged.

No replacement POI is inserted and no other visit is dropped. Solver-only retention
constraints are not stored as new explicit user locks. Routing attempts already
spent verifying the direct bypass carry into driving repair. The operation remains
inside the existing total timeout, solver and provider budgets.

A successful result is stored as a private E3-validated draft. “Yes” only confirms
preparation: the group must still approve the concrete proposal before publication.
Failed repair keeps the saved itinerary and includes the food/drink rule alongside
the actual optimizer, routing or timeout reason. English and Korean responses are
covered. Other kinds of direct-removal failures keep their existing behavior.

## Verification

Regression tests cover confirmation, direct removal, affected-day repair,
protected times, route budgets, unaffected dates, separate approval and failure
preservation. See [test commands](optimizer_verification.md).
