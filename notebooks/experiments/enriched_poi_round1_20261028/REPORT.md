# TravelSync enriched-POI experiment — round 1

Executed 2026-10-28 (Asia/Seoul). **All four methods ran. The enriched information can produce different scores within original-feature ties; recommendation relevance remains unevaluated. No winning method or production change is justified.**

## Scope and input audit

This is a curated feasibility study of 40 deliberately selected demo places, not a citywide sample or a real-user satisfaction study. The four supplied files were copied byte-for-byte into `inputs/`, hashed, and audited. The canonical v2 dataset was read for identity/feature checks only. Git revision: `635eef1cc611cbfb8f8a08c9acf67c168240bf78`. Freeze timestamp: `2026-10-28T02:00:57.175569+00:00`. Exact hashes and package inventories are in `manifest.json` and `results/run_manifest.json`.

Verified: 40 unique IDs; valid nonzero ten-dimensional binary vectors; all names, addresses, coordinates and feature values match the official dataset. The 40 places have **19 distinct feature vectors, 34 supplied descriptions, and seven groups containing at least two described places**. The missing-information input has 216 records. All 34 description_raw values are explicitly CORE_FACTS_PARAPHRASE; neither raw nor summary is a verbatim source document.

Every populated ranking field has at least one field-level provenance entry with a source and check date; none failed that structural check. This checks the supplied provenance, not the truth or current availability of each place. Ranking-field provenance contains 51 FULL_TEXT, 29 SEARCH_EXCERPT, 30 INHERITED_INPUT and 4 INHERITED_MAP entries (field-source records can overlap; these are not counts of independent places). Demo stay-time assumptions are excluded from ranking. `field_provenance.csv` retains every source record and limitation.

Six missing descriptions: 신선대유원지, 수정산가족체육공원, 광안3동 체육공원, 메이갤러리, 대성관온천, KT&G상상유니브 부산아틀리에.

Seven source CONFLICT flags: 다대포 꿈의 낙조분수, 오션갤러리, 이태석신부생가, 롯데월드 어드벤처 부산, 부산영화체험박물관, 남부메이커교육체험센터, 타보로. The primary policy excludes 이태석신부생가 (unresolved birthplace/memorial/address scope) and 타보로 (original café features versus restaurant attributes). Other conflicts concern operating conditions and remain recorded; they do not automatically exclude a preference-matching candidate. 윤산 retains only the supplied mountain-wide description; its uncertain starting point is not resolved here. Missing-description places with boundary/identity concerns remain excluded. `conflicts.csv` and `candidate_audit.csv` retain the details.

The shared primary subset is **19 places in six groups**. Exclusions total 21: six missing descriptions, two unresolved identity/attribute conflicts, and 13 candidates left in groups with fewer than two eligible members. Prices and hours are never eligibility requirements. All exclusions and exact IDs are in `candidate_audit.csv`; all eligible IDs are in `eligible_candidates.csv`.

Feature signature order: food, cafe, nature, history, art, shopping, activity, entertainment, relax, festival.

| Original feature signature | Described places | Primary places | Primary membership |
| --- | ---: | ---: | --- |
| `0000001000` | 2 | 2 | 키리안 수타소바체험교실, 남부메이커교육체험센터 |
| `0000001010` | 5 | 5 | 송정어울림체육공원, 화명오토캠핑장, 복병산체육공원, 제이스글램핑, 대저캠핑장 |
| `0001000000` | 3 | 2 | 국립일제강제동원역사관, 법륜사(부산) |
| `0010000010` | 6 | 6 | 성지곡산림욕장, 부산 송도해수욕장, 부산시민공원 농협기부숲, 동백섬, 광안리해수욕장, 윤산 |
| `0010000110` | 2 | 2 | 사직놀이공원, 황령산 유원지 생태숲 |
| `0100000000` | 2 | 0 | None: 타보로 category conflict; 컴포즈커피 becomes singleton |
| `1000000000` | 2 | 2 | 빨간떡볶이, 맛나감자탕 부산경남지사 |

NULL/blank are preserved as **unspecified unknown-or-not-applicable**, because the source uses NULL for both; the experiment cannot recover that distinction. Explicit UNKNOWN is kept distinct from explicit N/A/NOT_APPLICABLE (the latter tokens do not occur here). `missing_values.csv` preserves 1,473 unspecified-null and 86 explicit-unknown cells, not unique places. Omitted text fields become empty text only in model inputs; they never become negative attributes, zero prices or unrestricted hours.

## Frozen comparisons

Twelve synthetic Korean scenarios were frozen before comparative scoring: two each for food, making activities, camping/sports facilities, forest/beach experience, park classification/vegetation, and historical exhibitions/temples. `scenarios.json` contains stable IDs, full Korean statements, feature vectors, mapping rationale, evidence-place IDs and assumptions. Author-defined projections assign 5 to this candidate group's active categories and neutral 3 elsewhere. Thus A happens to score every primary pair at 5; identical vectors would tie for **any** valid preference vector. The mapping is not an observed survey response. Scenarios were not rewritten after observing rankings.

The Korean statements add user-side specificity absent from the original ten-category survey. Any later measured difference would combine richer user inputs, richer place information and method behavior; this design does not isolate a model-only effect.

- **A:** imports `score_individuals` from the actual production scorer. Formula `sum_j preference_j * binary_feature_j / sum_j binary_feature_j`, on the original 1–5 scale. No alternative normalization from a design document is used.
- **B:** cosine of character 2/3-gram TF-IDF over detailed_category, food_type, representative_menu and activity_type.
- **C:** exactly B's lexical procedure, adding description_summary. Each variant fits its vocabulary/IDF separately on the same 19 primary documents, excluding queries, once for all scenarios. Therefore B–C differences include vocabulary, IDF and length-normalization effects. `lexical_models.json` and `lexical_contributions.csv` expose the calculation.
- **D:** local CPU inference of `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` at revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`. Its official [model card](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) lists Apache-2.0 and sentence similarity/search usage; official [Sentence Transformers documentation](https://sbert.net/docs/sentence_transformer/pretrained_models.html) includes Korean among its supported languages. Mask-aware mean pooling produces 384 dimensions; L2-normalized embeddings use dot product as cosine. Inputs are truncated on the right at 128 total tokens including specials. **0 of 52 inputs were truncated** (40 documents plus 12 scenarios). CPU float32, eval mode, one torch thread, no prompts; exact model-file hashes, token lengths and embedding order are saved. Cosine is not a satisfaction probability or a calibrated 1–5 preference score.

B–D use the same cleaned Korean query. Text cleaning is NFKC, lowercase, punctuation to spaces, whitespace collapse; no translation, synonym expansion or inferred attributes. Only the listed fields enter text. candidate_type, selection_reason, A/B/C/D model scores, compromise ranks, names, addresses and provenance/selection metadata never enter model text. Names appear only in review/annotation outputs. Original text remains separate in the input snapshot. usage_conditions and target restrictions appear only as neutral annotation context; they are not ranking terms or feasibility filters.

All methods rank exactly the same candidates per primary scenario. Exact native-score equality defines visible score bands; descending score then ascending full place_id string is the fallback. ID order changes cannot establish relevance. No opening hours, prices or event dates are used to imply that any itinerary is feasible.

## Observed diagnostics

There are **38 scenario–place pairs and 152 method-score rows**. Across these scenarios there are 58 within-scenario candidate-pair comparisons; the same small groups recur, so these are not independent human observations.

| Method | Exactly tied pairs / 58 | Scenarios with all scores distinct |
| --- | ---: | ---: |
| A | 58/58 (100.0%) | 0/12 |
| B | 20/58 (34.5%) | 8/12 |
| C | 4/58 (6.9%) | 10/12 |
| D | 0/58 (0.0%) | 12/12 |

These are separation diagnostics, **not accuracy, relevance or satisfaction improvements**. A's score is 5 throughout; the text similarities have different scales and are not comparable to A's magnitude.

C changes fallback positions relative to B in 4/12 scenarios, with strict pair reversals in S05 and S08 as well as changes to tied bands. In S07 (편백/삼나무/숲 산책로), B has zero lexical overlap for all six candidates; C has nonzero scores for three and ranks 성지곡산림욕장 first. In S08, B leaves two beach candidates tied, C puts 부산 송도해수욕장 first, and D puts 광안리해수욕장 first. These differences need human judgments.

An unfavorable semantic result is retained: for S01's explicit 떡볶이 request, D scores 맛나감자탕 부산경남지사 0.56406 versus 빨간떡볶이 0.48206; B and C rank 빨간떡볶이 first. This is a source/query consistency concern, not an independently labeled error rate. It demonstrates why complete score separation cannot select D as the winner.

Missing-information sensitivity masks one primary candidate's description at a time while holding C's vocabulary/IDF and candidate set fixed: **8/38 masks change positions; 1/38 changes the top result** (S07, 성지곡산림욕장). Separate exploratory inclusion of all original candidates in the six tested groups yields 23 places and 184 score rows, with the same frozen lexical models. A's ID-fallback first result changes for S09/S10 when missing-description 신선대유원지 returns. D's first result changes for S11/S12 when the unresolved birthplace returns. This sensitivity deliberately includes excluded/missing-information rows and is not the shared primary evaluation; join candidate_audit.csv for their status. The excluded café group has no valid primary counterpart and is not evaluated through this sensitivity file.

## Human evaluation and validation

No independent scenario relevance labels were found in the supplied files or the repository's annotation/relevance search. Existing A/B/C/D scores are model outputs; feature-validation instructions and curation/manual approval do not supply relevance judgments. Neither the scenarios, rubric nor this report constitutes independent evidence.

Two independently shuffled, blinded CSV sheets contain the same 38 pairs, neutral source-supported place information, 0–3 relevance and insufficient-evidence fields. Both sets of human fields are blank. **NDCG and pairwise relevance agreement are NOT YET EVALUATED.** Give raters only their sheet and the Korean rubric, with no report, rankings, method identities or scores.

`evaluate.py` can be rerun after real annotations and collection provenance are supplied. It reports each rater and their mean, uses `k=min(3, actual group size)` (2 or 3 here), gain `2**grade-1`, log discounts, and tie-expected NDCG plus ID-fallback NDCG. Insufficient evidence excludes the complete scenario from NDCG; zero ideal gain is N/A. Pairwise agreement uses only unequal, sufficiently evidenced human grades: correct order=1, model tie=0.5, reversal=0, no applicable pair=N/A. Per-scenario exclusions remain visible. No final-label tuning is permitted.

Validation: **7 focused experiment tests passed; 40 repository tests passed, 2 skipped, 21 subtests passed.** The first pytest attempt hit a Windows temporary-directory cleanup permission error; rerunning with a workspace temporary directory completed successfully, with a nonfatal default-cache permission warning. The two complete inference runs are byte identical across **16 result files**, including embeddings, score bands, sensitivity diagnostics and annotation sheets. Canary perturbations verify exclusion of selection metadata; shared identities, finite scores, exact binary ties and blank human fields pass. All four original files, the canonical dataset and 153 pre-existing tracked files retain their hashes. No database was accessed by experiment code. Project dependencies/lockfile and production/application files were not changed. Credential scanning found no matching secret patterns in deliverable text; no credentials are used for inference. See `verification.json`, the result manifests and README for exact rerun commands.

## Recommendation

**Continue to a small, independent human relevance evaluation of the six primary groups.** This data supports that limited review, with insufficient-evidence responses allowed; it does not establish relevance on the full 40 or the city. No winning method should be chosen yet. Preserve the frozen results and collect two blinded ratings before making a ranking-quality claim.

Specific food/activity/experience preferences are needed beyond the original survey to test these distinctions. Next collect naturally elicited user statements, separately from these source-authored scenarios, and expand source-verified descriptions for the six missing places. Resolve the birthplace and café/restaurant conflicts; improve attribute specificity only where sources support it. Sparse descriptions must not be filled by inference. Current access, event and operating conditions require a separate feasibility experiment.

A later AWM/GFAR experiment is justified by independently collected per-member relevance evidence, adequate inter-rater consistency/coverage, and repeatable gains on broader held-out scenarios over the binary tied baseline and lexical controls, with uncertainty and unfavorable cases retained. Predefine the split and comparison before using held-out labels. Use shared candidate pools and list lengths, inspect each member and worst-member utility, veto/empty/short-list rates, and missing-data effects. Validate any semantic-to-1–5 calibration on separate data before applying AWM's existing thresholds; cosine must not be inserted as a satisfaction score. Group experiments then need consented real group preferences and independent outcomes. This round alone supplies none of that validation.
