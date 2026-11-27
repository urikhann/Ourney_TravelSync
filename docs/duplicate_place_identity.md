# Duplicate physical-place visits

## Confirmed cause

Read-only inspection of LALALA's saved itinerary 34 (optimization audit 70) found
`tour:129631` at 11:10–12:40 and `kakao:12567237` at 20:17–21:47. Both are named
경상좌수영성지 and identify 수영성로 43. The Tour record uses the expanded 부산광역시
address and a trailing 수영동 annotation; Kakao uses 부산. Their source coordinates
differ, so proximity alone would not reliably identify this pair.

Both catalogue records reached the same optimizer request. The existing solver
and E3 validator enforced uniqueness by source POI ID only. Because the IDs differ,
the second record could earn a second visit's utility without triggering the old
duplicate-ID rule. This was a real saved-plan duplicate, not a timeline rendering
issue or a second copy of the same ID.

## Correction

Candidates now carry an additional visit identity derived from a normalized exact
name and numbered address in the pinned catalogue. Source IDs, scores, operational
facts and catalogue rows remain intact. Regional long/short forms and a trailing
Korean neighborhood annotation normalize consistently; branch names, house-number
hyphens and unit/floor details remain meaningful. Missing addresses, name-only
matches and nearby coordinates do not establish shared identity.

CP-SAT limits all aliases of one identity to one visit across the trip. Independent
E3 rejects repeated identities with `DUPLICATE_PLACE`. Repair and gap-fill candidate
selection also avoid already-visited aliases. Legacy private replay/draft requests
derive identities from their own pinned POI version when used for new proposals
or approval; no newest-catalogue substitution occurs. Conflicting explicit locks
remain constraints and are never silently removed.

The captured saved plan now fails E3 with exactly the second alias's duplicate-place
error. A provisional replay using the same candidates and locks selected only
`tour:129631` and preserved every explicit lock. Provisional geography is not road
verification; no replacement live itinerary was published and no live DB data was
modified. Existing saved schedules remain as recorded until the user requests a new
validated plan or approves a feasible repair.

Identity matching is deliberately conservative: differently spelled names or
incomplete/different addresses can remain unresolved aliases. This correction does
not merge or activate catalogue data, or prove that source coordinates are accurate.

Verification includes source-alias normalization, distinct branches/addresses,
trip-wide uniqueness, explicit-lock preservation, independent E3 rejection,
localized repair, gap-fill exclusion and pinned-catalogue replay upgrades. Final
root/API suites passed; see issues_16_29_31_audit.md for counts and limitations.
