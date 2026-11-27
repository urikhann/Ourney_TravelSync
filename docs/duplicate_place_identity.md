# Duplicate physical-place visits

Candidates carry an additional visit identity derived from a normalized exact
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

Identity matching is deliberately conservative: differently spelled names or
incomplete/different addresses can remain unresolved aliases. This correction does
not merge or activate catalogue data, or prove that source coordinates are accurate.

See [test commands](optimizer_verification.md) for identity, solver and repair regressions.
