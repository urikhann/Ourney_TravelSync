# Option C versus binary baseline: new evaluation

Date: 2026-10-31. **Full-catalogue superiority is not established.** All scores and metrics below were newly computed from current production scorer code and the enriched 3,570-POI CSV. No previous NDCG or ranking result was reused.

## Validity and comparable conditions

All methods score the same eligible catalogue, then restrict to the exact same historically judged pool per scenario. Option C vocabulary/IDF is fitted on all eligible catalogue documents, never on queries, judgments or the small judged pool. Config is fixed in protocol.json; no result selects a tokenizer, field weight, threshold or algorithm. Empty exclusion reasons leave all 3,570 rows eligible; no trip timing or budget is invented.

The primary binary comparator is the original SDD weighted sum, reused from services/mocks/group_rec.py: sum(r_k*x_k)/sum(r_k). The package binary scorer instead averages preferences over active POI features: sum(r_k*x_k)/sum(x_k); its separately named reference rows are included. These differ across the catalogue but both tie inside each original feature-matched judged pool.

Twelve Korean scenarios and their synthetic 1-5 feature projections are reused unchanged. They are not collected user survey/query pairs. TF-IDF receives detailed text; binary features cannot express those details. This compares the information/interface settings as well as the scorers.

Two 38-pair sheets are from **one blinded human**, not two independent raters or consensus ground truth. Each is reported separately. Human source hashes and adapters were verified against the frozen packet. Eighteen of 19 POIs have changed scoring text since annotation. Historical-label transfer is a descriptive diagnostic, not freshly validated current-corpus relevance. Only one POI has exact scoring-text agreement, leaving zero nondegenerate strict-evidence scenarios. Even exact text agreement alone would not establish current real-world relevance.

No full-catalogue NDCG or Recall is reported: nearly all scenario–POI pairs are unjudged. Full-catalogue Precision@K is emitted only if every top-K item is judged; otherwise null. MRR is also withheld on incompletely judged full rankings. Unknown items never become grade zero. The small-pool metrics below do not establish catalogue retrieval quality.

## Retrospective judged-pool results (primary threshold >=2)

Gain=2^grade-1, logarithmic DCG discount. NDCG reuses the original evaluator function unchanged. Primary scores average uniformly over exact ties; seeded production ordering (seed 0) is a separate diagnostic. Precision divides by requested K even for short lists; Recall divides by known relevant items in that judged pool only. No-relevant Recall/MRR and zero-IDCG NDCG are N/A. Grade thresholds >=1 and >=3 are sensitivity rows, never alternatives chosen after results. MRR is over the complete judged pool and repeated for each cutoff for tabular convenience.

| Sheet | Method | K | NDCG | Precision | Recall | MRR |
|---|---|---|---|---|---|---|
| A | option_c | 5 | 0.9659 | 0.3667 | 1.0000 | 0.9583 |
| A | option_c | 10 | 0.9660 | 0.1833 | 1.0000 | 0.9583 |
| A | binary_weighted_sum_sdd | 5 | 0.8307 | 0.3500 | 0.9722 | 0.8000 |
| A | binary_weighted_sum_sdd | 10 | 0.8412 | 0.1833 | 1.0000 | 0.8000 |
| A | binary_active_mean_package | 5 | 0.8307 | 0.3500 | 0.9722 | 0.8000 |
| A | binary_active_mean_package | 10 | 0.8412 | 0.1833 | 1.0000 | 0.8000 |
| B | option_c | 5 | 0.9576 | 0.3500 | 1.0000 | 0.9583 |
| B | option_c | 10 | 0.9580 | 0.1750 | 1.0000 | 0.9583 |
| B | binary_weighted_sum_sdd | 5 | 0.8162 | 0.3306 | 0.9722 | 0.7675 |
| B | binary_weighted_sum_sdd | 10 | 0.8266 | 0.1750 | 1.0000 | 0.7675 |
| B | binary_active_mean_package | 5 | 0.8162 | 0.3306 | 0.9722 | 0.7675 |
| B | binary_active_mean_package | 10 | 0.8266 | 0.1750 | 1.0000 | 0.7675 |

Every scenario has only 2–6 judged candidates. At K=10 all are retrieved, so Recall@10 is 1 where applicable and cannot distinguish methods; short-list Precision@10 mostly reflects pool size. At K=5, only the six-candidate scenarios permit membership differences. NDCG/MRR carry most of the ordering information here. These are not standard full-catalogue Top-5/10 benchmark results.

## Categories and scenario differences

| Sheet | Category | Method | NDCG@5 | MRR |
|---|---|---|---|---|
| A | activity | option_c | 1.0000 | 1.0000 |
| A | activity | binary_weighted_sum_sdd | 0.8155 | 0.7500 |
| A | activity+relax | option_c | 0.9586 | 1.0000 |
| A | activity+relax | binary_weighted_sum_sdd | 0.8021 | 0.7125 |
| A | food | option_c | 1.0000 | 1.0000 |
| A | food | binary_weighted_sum_sdd | 0.8860 | 0.8750 |
| A | history | option_c | 0.8549 | 0.7500 |
| A | history | binary_weighted_sum_sdd | 0.8549 | 0.7500 |
| A | nature+entertainment+relax | option_c | 1.0000 | 1.0000 |
| A | nature+entertainment+relax | binary_weighted_sum_sdd | 0.9170 | 1.0000 |
| A | nature+relax | option_c | 0.9821 | 1.0000 |
| A | nature+relax | binary_weighted_sum_sdd | 0.7085 | 0.7125 |
| B | activity | option_c | 1.0000 | 1.0000 |
| B | activity | binary_weighted_sum_sdd | 0.8549 | 0.7500 |
| B | activity+relax | option_c | 0.9567 | 1.0000 |
| B | activity+relax | binary_weighted_sum_sdd | 0.7292 | 0.7125 |
| B | food | option_c | 1.0000 | 1.0000 |
| B | food | binary_weighted_sum_sdd | 0.8549 | 0.7500 |
| B | history | option_c | 0.8549 | 0.7500 |
| B | history | binary_weighted_sum_sdd | 0.8549 | 0.7500 |
| B | nature+entertainment+relax | option_c | 1.0000 | 1.0000 |
| B | nature+entertainment+relax | binary_weighted_sum_sdd | 0.8860 | 0.8750 |
| B | nature+relax | option_c | 0.9342 | 1.0000 |
| B | nature+relax | binary_weighted_sum_sdd | 0.7171 | 0.7674 |

Scenario-wise higher/equal/lower reflects one person’s historical grades, not independent replications.

| Sheet | Scenario | Category | C NDCG@5 | Binary NDCG@5 | Delta | Outcome |
|---|---|---|---|---|---|---|
| A | S01 | food | 1.0000 | 0.9170 | 0.0830 | C higher |
| A | S02 | food | 1.0000 | 0.8549 | 0.1451 | C higher |
| A | S03 | activity | 1.0000 | 0.8155 | 0.1845 | C higher |
| A | S04 | activity | 1.0000 | 0.8155 | 0.1845 | C higher |
| A | S05 | activity+relax | 1.0000 | 0.8620 | 0.1380 | C higher |
| A | S06 | activity+relax | 0.9173 | 0.7423 | 0.1750 | C higher |
| A | S07 | nature+relax | 0.9643 | 0.7014 | 0.2629 | C higher |
| A | S08 | nature+relax | 1.0000 | 0.7156 | 0.2844 | C higher |
| A | S09 | nature+entertainment+relax | 1.0000 | 0.9170 | 0.0830 | C higher |
| A | S10 | nature+entertainment+relax | 1.0000 | 0.9170 | 0.0830 | C higher |
| A | S11 | history | 1.0000 | 0.8549 | 0.1451 | C higher |
| A | S12 | history | 0.7098 | 0.8549 | -0.1451 | binary higher |
| B | S01 | food | 1.0000 | 0.8549 | 0.1451 | C higher |
| B | S02 | food | 1.0000 | 0.8549 | 0.1451 | C higher |
| B | S03 | activity | 1.0000 | 0.8549 | 0.1451 | C higher |
| B | S04 | activity | 1.0000 | 0.8549 | 0.1451 | C higher |
| B | S05 | activity+relax | 0.9575 | 0.7953 | 0.1622 | C higher |
| B | S06 | activity+relax | 0.9558 | 0.6631 | 0.2927 | C higher |
| B | S07 | nature+relax | 1.0000 | 0.7327 | 0.2673 | C higher |
| B | S08 | nature+relax | 0.8683 | 0.7014 | 0.1669 | C higher |
| B | S09 | nature+entertainment+relax | 1.0000 | 0.8549 | 0.1451 | C higher |
| B | S10 | nature+entertainment+relax | 1.0000 | 0.9170 | 0.0830 | C higher |
| B | S11 | history | 1.0000 | 0.8549 | 0.1451 | C higher |
| B | S12 | history | 0.7098 | 0.8549 | -0.1451 | binary higher |

Sheet A C higher / tie / binary higher: 11 / 0 / 1.

Sheet B C higher / tie / binary higher: 11 / 0 / 1.

Selected cases (current scores, historically judged alternatives only):

- S01: 떡볶이를 먹을 수 있는 분식집에 가고 싶어요. Option C orders 빨간떡볶이 (historical sheet B grade 3, corpus rank 4) > 맛나감자탕 부산경남지사 (historical sheet B grade 1, corpus rank 1511). The binary scorer ties these alternatives.
- S03: 메밀가루로 수타 소바를 직접 만들어 보고 싶어요. Option C orders 키리안 수타소바체험교실 (historical sheet B grade 3, corpus rank 1) > 남부메이커교육체험센터 (historical sheet B grade 1, corpus rank 598). The binary scorer ties these alternatives.
- S07: 편백과 삼나무가 자라는 숲의 산책로를 걷고 싶어요. Option C orders 성지곡산림욕장 (historical sheet B grade 3, corpus rank 1) > 동백섬 (historical sheet B grade 2, corpus rank 2) > 윤산 (historical sheet B grade 2, corpus rank 35) > 부산시민공원 농협기부숲 (historical sheet B grade 2, corpus rank 148) > 부산 송도해수욕장 (historical sheet B grade 1, corpus rank 444) > 광안리해수욕장 (historical sheet B grade 1, corpus rank 511). The binary scorer ties these alternatives.
- S12: 사찰을 둘러보며 절의 역사를 알아보고 싶어요. Option C orders 국립일제강제동원역사관 (historical sheet B grade 1, corpus rank 3) > 법륜사(부산) (historical sheet B grade 3, corpus rank 68). The binary scorer ties these alternatives.

S12 is the counterexample on both sheets: the lower-graded museum outranks the temple. The history category mean therefore ties the binary expectation across S11/S12. Other categories show higher retrospective mean NDCG. These observations do not prove why a scorer made a particular error.


## Coverage and missing metadata (separate from relevance)

| Scenario | Query n-gram coverage | OOV | Positive-score POIs | Zero-score fraction | Judged POIs |
|---|---|---|---|---|---|
| S01 | 0.8444 | False | 1820 | 0.4902 | 2 |
| S02 | 0.7209 | False | 403 | 0.8871 | 2 |
| S03 | 0.7755 | False | 1687 | 0.5275 | 2 |
| S04 | 0.7391 | False | 1866 | 0.4773 | 2 |
| S05 | 0.7778 | False | 1760 | 0.5070 | 5 |
| S06 | 0.8596 | False | 2327 | 0.3482 | 5 |
| S07 | 0.8627 | False | 1774 | 0.5031 | 6 |
| S08 | 0.7736 | False | 2348 | 0.3423 | 6 |
| S09 | 0.7333 | False | 2211 | 0.3807 | 2 |
| S10 | 0.8955 | False | 2019 | 0.4345 | 2 |
| S11 | 0.8000 | False | 1776 | 0.5025 | 2 |
| S12 | 0.6000 | False | 1674 | 0.5311 | 2 |

Query coverage is in-vocabulary character n-gram occurrences divided by all query n-gram occurrences. OOV means a nonempty cleaned query transforms to an all-zero vector; partial OOV is visible in the coverage fraction. Positive-match coverage does not establish relevance. Candidate coverage tables separately report how many historically judged/relevant IDs occur in full-catalogue Top-5/10/20/100; they are not global recall.

12/12 queries have a positive match; entirely OOV queries=0. Across 42,840 query–POI pairs, Option C has 49.4281% zero scores; both binary scorers have no zero scores. Nonzero binary values also do not establish relevance. Full-catalogue Precision@5/10 is unavailable for every scenario/method because no returned list is fully judged.

Candidate coverage below is the mean fraction of historical grade>=2 IDs retrieved in the full catalogue. Seeded retrieval uses production seed 0; expected retrieval averages exact-score ties to show the baseline tie sensitivity. Neither measures recall over unknown relevant POIs.

| Sheet | Method | Catalogue cutoff | Known relevant coverage (seed 0) | Tie expectation |
|---|---|---|---|---|
| A | option_c | 5 | 0.6250 | 0.6250 |
| A | option_c | 10 | 0.6528 | 0.6528 |
| A | option_c | 100 | 0.8194 | 0.8194 |
| A | binary_weighted_sum_sdd | 5 | 0.1667 | 0.1667 |
| A | binary_weighted_sum_sdd | 10 | 0.1667 | 0.1667 |
| A | binary_weighted_sum_sdd | 100 | 0.3889 | 0.3523 |
| A | binary_active_mean_package | 5 | 0.0000 | 0.0329 |
| A | binary_active_mean_package | 10 | 0.0833 | 0.0658 |
| A | binary_active_mean_package | 100 | 0.3889 | 0.4276 |
| B | option_c | 5 | 0.7222 | 0.7222 |
| B | option_c | 10 | 0.7500 | 0.7500 |
| B | option_c | 100 | 0.9097 | 0.9097 |
| B | binary_weighted_sum_sdd | 5 | 0.1667 | 0.1667 |
| B | binary_weighted_sum_sdd | 10 | 0.1667 | 0.1667 |
| B | binary_weighted_sum_sdd | 100 | 0.3819 | 0.3523 |
| B | binary_active_mean_package | 5 | 0.0000 | 0.0329 |
| B | binary_active_mean_package | 10 | 0.0833 | 0.0658 |
| B | binary_active_mean_package | 100 | 0.3819 | 0.4276 |
| Field | Nonempty POIs | Missing POIs |
|---|---|---|
| detailed_category | 3545 | 25 |
| food_type | 1630 | 1940 |
| representative_menu | 477 | 3093 |
| activity_type | 0 | 3570 |
| description_summary | 1835 | 1735 |

There are 25 empty five-field documents; these remain eligible and score zero. The current CSV has representative_activity but no activity_type. The strict implementation does not alias it, so that entire input field is absent. Missing values are not negative relevance evidence.

One-field omission diagnostics refit the eligible corpus without that field, holding queries, identities and parameters fixed; no configuration is selected from these results.

| Masked field | Top-1 changes / 12 | Mean Top-10 overlap | Mean added-zero POIs |
|---|---|---|---|
| detailed_category | 3 | 0.7917 | 165.8333 |
| food_type | 1 | 0.9833 | 0.6667 |
| representative_menu | 0 | 0.9833 | 1.2500 |
| activity_type | 0 | 1.0000 | 0.0000 |
| description_summary | 10 | 0.2750 | 1593.0000 |

## Interpretation and recommendations

Implementation correctness and recommendation quality are separate. Direct scikit-learn cosine checks, repeat runs, query-batch independence, audited label alignment and protected-input hashes test correctness. They do not validate relevance. The historical descriptive advantages or losses above cannot establish improvement for current users, catalogue retrieval, GFAR group outcomes or itinerary quality.

Collect a new independently blinded pooled judgment set for the current catalogue: include Top-K candidates from both fixed methods, sampled nonretrieved items and relevant alternatives; keep unjudged items explicit. Use real paired preference text and ten-feature surveys, prospectively freeze scenarios, relevance definitions and tie policies, and obtain more than one independent person. For valid catalogue Recall/NDCG, use a sufficiently judged declared universe or a defensible incomplete-judgment protocol; Top-K pooling alone cannot prove total relevant recall.

Review the absent activity_type schema field and missing descriptions using verified sources before any separately authorized production change. Any future mapping, text enrichment or tuning needs held-out evaluation; this run changes none of them. Avoid interpreting cosine as satisfaction or calibrating from these reused labels. Reassess candidate coverage before studying GFAR.

Known unresolved rubric issues are retained without relabeling:
- S01 gamjatang: sheet A grade 2 with note describing only broad Korean-restaurant relation may conflict with rubric grade 2 requirement of direct partial core preference evidence; preserved without adjudication. Sheet B grade 1.
- S05 Daejeo camping: sheet B note says no overnight-stay information but assigns 2/NO. Relevance to partial camping experience versus missing overnight evidence requires human clarification; not automatically treated as missing.

Limitations: one annotator; synthetic queries authored from the old POI evidence (not held-out); tiny feature-matched judged pools; repeated POIs/dependent scenarios; historical evidence drift; AI-assisted English annotation translation; no current availability verification; zero strict evidence-comparable rankings. Previous exposure of these scenarios during development means this is a reused diagnostic set, never a held-out test. No significance or causal improvement claim is made.

## Reproducibility and files

Run evaluate_tfidf.py --destination <fresh directory>; existing outputs are never overwritten. protocol.json fixes all settings. manifest.json records hashes of the current code/data, frozen inputs, original human sheets, legacy evaluator source, fitted vocabulary/IDF and environment. All pre-existing tracked files and protected source files must match before/after. Output hashes support an independent clean repeat. Production recommendation code, GFAR, constraints, dataset and labels remain unchanged.

summary.csv/json: sheet/category means; per_scenario_metrics.csv: thresholds and tie policies; scenario_comparison.csv: wins/losses; full_catalogue_metrics.csv: explicitly unavailable metrics and judgment coverage; query_diagnostics.csv; score_diagnostics.csv; candidate_coverage.csv; metadata_coverage.csv; missing_field_sensitivity.csv; observed_missing_metadata.csv; evidence_drift.csv; rankings.csv: fresh full-catalogue scores/ranks; judged_rankings.csv: grades and pool/corpus ranks; strict_evidence_metrics.json: insufficient-evidence result. All CSV empty numeric cells correspond to JSON null/N/A, never fabricated zeros.
