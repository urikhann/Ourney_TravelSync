# Seeded identity ties

Ranking uses descending score, then SHA256 of `str(seed) + NUL + stable identity`,
then identity as collision fallback. Seeds change ties, never strict score order.
Candidate rows also use seeded identity order. Selected identities and aligned
matrices survive input-row reordering. End-to-end tests cover equal/varied inputs.

Persisted individual ranks use the same policy. Direct AWM/GFAR calls with a
caller-built GroupInput retain input-order ties; the standard pipeline supplies
seeded candidate order, resolving Borda/selection-gain ties reproducibly too.
CSV workflows use external place IDs; DB workflows use stable UUIDs. Tied outputs
across those identity domains need not match; each is reproducible independently.

This removes accidental CSV-order dependence. It does not enforce source quotas
or prove improved relevance/fairness. Category-identical POIs remain
indistinguishable to the scoring model.

Use `uv run --locked python scripts/tie_diagnostics.py` for diagnostics on the packaged catalogue.
