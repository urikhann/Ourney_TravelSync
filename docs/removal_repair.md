# Removing an activity between food/drink stops

## Confirmed LALALA failure

The saved confirmation question (chat 160) identified `remove_poi` for the concert
hall at 17:52 on 2026-11-30. Chat 161's “Yes” consumed that exact pending action.
Chat 162 retained the precise validator error:
`2026-11-30::CONSECUTIVE_FOOD_DRINK`. This was an application constraint failure,
not a lost confirmation, final-model failure or provider outage.

The locked 단테라운지 visit was 16:47–17:32, followed by the unlocked 경성대학교
콘서트홀 at 17:52–19:22 and locked dinner at 면식당 부산부경대점, 19:26–20:26.
Deleting the hall left cafe and dinner consecutive. `separate_food_drink` was true.
The removal path rebuilt the direct connection at the existing times and stopped
at E3; it did not attempt a rearrangement.

## Correction

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

## Captured-case replay

Read-only DB inspection and a read-only route-cache query provided the original
request and 35 saved directed road edges. A local replay removed only
`kakao:8183550`, required the nine remaining visits and fixed all seven protected
times. It returned `OPTIMAL` / `PASSED` with no E3 errors. 부산 중앙공원 moved to
17:38–18:38, separating cafe and dinner. No external provider request was made.

This replay uses captured opening facts and existing route-cache evidence. It is
not a newly refreshed live proposal. No live itinerary, chat, vote or DB settings
were modified; the earlier confirmation remains consumed and requires a new
request to prepare a fresh proposal.

## Regression coverage

Tests exercise persisted confirmation followed by Yes, walking and selected-edge
driving repair, unchanged POI set except the removed target, exact protected times,
no implicit votes/publication, routing-attempt continuity, unchanged second date
through explicit approval, infeasibility under fixed activities, and provider
timeout with the original constraint reason preserved.

Removal/optimizer integration checks passed all 30 tests. The optimizer integration
module pins its default test seed so transport/constraint assertions do not depend
on random trip UUIDs reaching the two-second solver limit; search budgets and
assertions remain unchanged. The final issue audit passed the full API suite
(210 passed, 2 skipped). See issues_16_29_31_audit.md for complete verification.
