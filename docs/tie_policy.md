# Seeded identity ties on the official v2 snapshot

Reproduce with `uv run python scripts/tie_diagnostics.py`. Machine-readable output
is committed in `tie_diagnostics.json`. These measurements use seed 0 and top-100
per member on the official byte-verified 3,570-row v2 snapshot.

There are 21 distinct feature vectors. Equal preferences produce one score for
all 3,570 POIs. For the two varied members there are 9 and 8 distinct scores;
respectively 3,569 and 3,570 rows belong to a score tie. Their top-100 cutoff ties
contain 797 and 649 POIs, both at score 5.

| Scenario and policy | Union size | TOUR_API | TOUR_API+KAKAO | KAKAO |
|---|---:|---:|---:|---:|
| Equal preferences, historical CSV-order cutoff | 100 | 77 | 23 | 0 |
| Equal preferences, seeded identity cutoff | 100 | 13 | 3 | 84 |
| Varied preferences, historical CSV-order cutoff | 200 | 74 | 38 | 88 |
| Varied preferences, seeded identity cutoff | 200 | 10 | 4 | 186 |

The varied profiles in feature order are `[4,5,3,2,2,3,3,3,4,1]` and
`[3,2,5,4,3,2,4,2,5,3]`. Equal uses two all-3 members. Counts refer to candidates,
not the final recommendation list.

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
