# GFAR implementation audit — 2026-10-30

The production GFAR implements the published Borda/coverage equations correctly. The newer offline C+GFAR implementation uses the same recurrence with explicit tie and boundary extensions. A decline in minimum member utility does not establish an implementation error: neither implementation explicitly maximizes that metric.

No recommendation code, existing experiment outputs, database contents, or canonical data were modified. The audit adds this report and `check_equations.py` only.

## Verification performed

- Production algorithm/pipeline/tie tests: 19 passed, 21 subtests passed.
- Existing C+GFAR experiment tests: 28 passed.
- New direct-set-function check: 300 fixed-seed random fixtures, 4,492 selection steps across production and experimental implementations. Every selected item maximized the direct objective difference within the experimental rounding tolerance, and every reported gain matched it. Fixtures include continuous scores, exact score ties, zero K, and K beyond candidate count; groups have 2–8 members. This is equation validation, not relevance evaluation or full Java execution.
- Paper example: selected i1, i4, i2; equation-derived gains 4/3, 2/3, 2/9. The extracted Table 2 reports 2/27 for the third gain; the recurrence gives 2/9. Do not alter the recurrence to match that table entry.
- Recomputed C-rank summary and paired counts directly from the saved primary per-group CSV; did not rerun the entire enrichment/scoring experiment.
- Hashes of production GFAR, canonical v2 data, and offline aggregation were unchanged before/after the new checks.

Reproduce from the repository root:

```powershell
.venv/Scripts/python.exe audits/gfar_2026-10-30/check_equations.py
```

## Mathematical alignment

For personal-list depth L and 1-based personal rank r, the production weights are `(L-r)/(L*(L-1)/2)`, with zero relevance outside the list. This includes zero relevance at rank L, as specified by the Borda definition.

For a selected set S, define `F(S) = sum_u (1 - product_{j in S}(1-p[u,j]))`. Production computes `gain(i) = sum_u q[u]*p[u,i]`, selects a maximum, then updates `q[u] *= 1-p[u,i]`. Direct differences `F(S+i)-F(S)` agree with its gains. Experimental aggregation has the same property within its gain-rounding tolerance.

The paper's experimental protocol uses personal depth 20 and output length 20. Production defaults to depth 20 but commonly uses K10 (package) or K30 (service); the C experiment uses K10. These settings are adaptations, not the original experimental protocol.

Source: [published paper, Sections 3 and 4](https://cora.ucc.ie/server/api/core/bitstreams/a40b7c99-11c9-46c9-a5af-be7b93f66b61/content).

## Authors' released code differs from the equation

Inspected the public master-branch Java sources live on 2026-10-30:

- [GFARAspectModel.java](https://github.com/mesutkaya/recsys2020/blob/master/GFAR_java/src/main/java/gfar/rerank/GFARAspectModel.java), Git blob `e2c6a77e72f5757f6a4bb1e9d1327d4ba85b3956`.
- [AlphaGFAR.java](https://github.com/mesutkaya/recsys2020/blob/master/GFAR_java/src/main/java/gfar/rerank/AlphaGFAR.java), Git blob `566cd85a01415041553c1608dfb1f019ed8f5cb5`.
- [GFARIntentModel.java](https://github.com/mesutkaya/recsys2020/blob/master/GFAR_java/src/main/java/gfar/rerank/GFARIntentModel.java), Git blob `b724c18e8d41ef052367f18102245abc80047701`.
- [RunGFAR.java](https://github.com/mesutkaya/recsys2020/blob/master/GFAR_java/src/main/java/gfar/RunGFAR.java).

In the aspect model, normalization accumulates the entire personal Borda sum once for every item in the supplied group candidate list. With M supplied candidates and complete personal lists, this produces `p_java = p_equation/M`. The redundancy update then uses that smaller probability. Consequently, this is not just a constant scaling of reported gains: after the first pick it weakens diminishing coverage and can change subsequent selections. The equality-mode user weight `1/group_size` is, by contrast, a harmless common scaling for selection within one group. The supplied runner uses alpha=1, lambda=1 and personal depth 20.

This normalization issue is a source-level finding; the full Java/RankSys pipeline was not executed. Your production code follows the published normalization rather than this apparent released-code defect. Exact reproduction of the authors' released experiment and compliance with the published equations are separate claims.

## Why a correctly implemented GFAR can hurt the minority

GFAR sums member coverage gains; it does not maximize the minimum member score or impose a minority representation constraint. Three members sharing a preference contribute three terms. Deep personal lists make each probability small (maximum `2/L`), slowing the reduction of their influence. Rank conversion also discards absolute dislike: a rating of 1 contributes zero if outside a personal list, rather than a negative penalty against selecting that item for others.

A new controlled fixture uses 200 candidates: 100 majority items rated 5 by three identical members and 1 by the minority; 100 minority items have the opposite ratings. K is fixed at 10. All identities, scores, and list lengths are shared across the depth comparisons.

| Personal depth | Minority items selected | Each majority member's mean | Minority mean |
| --- | ---: | ---: | ---: |
| 10 | 3 | 3.8 | 2.2 |
| 20 | 1 | 4.6 | 1.4 |
| 50 | 0 | 5.0 | 1.0 |
| 100 | 0 | 5.0 | 1.0 |

This demonstrates a limitation of the objective/settings, without requiring a coding bug. It does not establish which depth is best for real groups. In the saved C experiment, depth100 instead improves the aggregate minimum-utility summary; depth effects are data-dependent.

## Current experiment result confirmed

Recomputed from `data/generated/evaluation/c_gfar_20261030_run01/per_group_results.csv`, primary C scorer, C-rank assessor, percentile calibration, shared candidate pools, K10, depth20, 105 groups:

| Metric averaged across groups | C+AVG | C+GFAR |
| --- | ---: | ---: |
| Minimum member utility | 0.515247 | 0.475565 |
| Mean member utility | 0.650815 | 0.640027 |
| Member utility gap | 0.249663 | 0.246482 |
| Jain index | 0.932618 | 0.942037 |

Minimum member utility declines by 7.70%; GFAR improves 28 groups, ties 15, worsens 62. Gap and Jain improve slightly. These metrics disagree, so the precise finding is worse worst-member simulated rank utility, not a decline under every fairness definition. Both algorithms return ten items for all 105 groups. The evaluator is scorer-derived, discounted top100 rank utility, whereas selection uses depth20 Borda coverage. It is not independent human satisfaction or the paper's held-out relevance evaluation.

## Other differences and diagnostic issues

1. **Tie sensitivity:** production personal score and gain ties follow candidate order; the standard pipeline supplies seeded identity order. The experiment uses ID order for personal ties and rounded gains, calibrated mean, then ID for selection ties. These are different implementations at ties, not different coverage recurrences. On category scoring, many genuinely indistinguishable items receive different rank weights solely through the tie convention.
2. **Candidate support:** a cutoff can remove compromise items before aggregation. The standard top100 union preserves depth20 personal support when the same rankings/ties are used, but it need not preserve compromises. The C experiment shares the A/C top100 union across algorithms, so different candidate sets do not explain its primary within-C contrast.
3. **Zero-gain tails:** production fills to K even when all remaining gains are zero, choosing by input order. Those items have no preference support under this GFAR model; their raw scores can change satisfaction metrics. Log zero-gain selections explicitly.
4. **Service diagnostic mismatch:** `services/recommender/main.py` computes `per_member_relevance` as a sum of selected Borda probabilities. GFAR's modeled coverage is `1-product(1-p)`. The sum is a different diagnostic and must not be interpreted as its coverage probability. For one candidate, the service's explanation Borda matrix is all zero, while production explicitly returns a positive singleton gain; that is a genuine boundary inconsistency in diagnostics, not an explanation for the 105-group C experiment.
5. **Extensions:** singleton behavior, clipping depth to the available candidates, and the experiment's single-member personal-order rule should be described as extensions. Random equation checks here deliberately use at least two members and candidates; existing suites cover boundaries.

## Recommended next step

Retain the published recurrence. Add explicit logging of per-member coverage, support depth, zero-gain tail count, and rank/tie choices before any further comparison. Keep worst-member utility and relevance/coverage metrics separate. A prospective paper-protocol condition can use depth20/K20 with held-out human relevance; do not select whichever normalization or depth wins the existing simulator. If protecting the worst-off member or requiring an acceptable score for every visited POI is the product requirement, compare an explicit max-min or constrained method on fixed candidates and independent judgments, naming it as a different objective rather than a GFAR bug fix.
