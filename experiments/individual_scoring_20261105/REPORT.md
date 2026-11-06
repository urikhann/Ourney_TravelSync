# TravelSync individual scoring experiment

Study date: 2026-11-05. Decision: retain the structured active-feature mean as the simplest defensible individual baseline. SentenceTransformer adds ranking distinctions from names, fine categories and stored descriptive text, but this study does not establish that those distinctions improve relevance enough to justify production deployment. No production code, database, schema, SRS/SDD or dataset was modified.

## What actually ran

Two immutable multilingual model checkpoints, three POI representations, weighted and centroid profiles, beta 0/0.5/1, four dislike penalties, five hybrid alphas including endpoints, and two normalization rules ran locally. The runner produced **104 scoring configurations**, **852 executable technical group comparisons** and **396 explicitly inapplicable semantic/rank-scale AWM comparisons** (1,248 total comparison rows). Eight executable comparisons exhausted their candidate pool; 844 produced nonempty lists. Models were not trained or fine-tuned. Ground-truth quality evaluation and satisfaction calibration did not run.

There are 16 synthetic preference profiles: all-neutral, all-4, all-1, all-5, then 12 seeded random profiles. Four fixed groups use four consecutive profiles each. Group 0 intentionally contains an all-disliked member and is a diagnostic stress test, not a representative population. Seed is 20261105. POIs are sorted lexicographically by stable ID; ties use this order. Group pools contain the full catalogue minus two fixed ID exclusions (3,568 candidates); no top-N union can confound the aggregation comparison. GFAR depth is 20 and returned K is 10.

## Phase 1: implementation audit

The requested formula already exists in `src/ourney_travelsync/recommenders/individual_scoring.py::score_individuals`: `users @ features.T / active_feature_count`, on the survey 1–5 scale. The production validator rejects all-zero feature rows. The isolated scorer instead flags them as unclassified and returns NaN, which is removed with an explicit cause before aggregation.

The active method depends on the entry point. `recommenders/pipeline.py::prepare_group` defaults to binary scoring; `cli.py::parser` and `api/app/routes/recommendations.py::RunIn` default to TF-IDF. `cli.py::recommendation_config` chooses 0.25 for TF-IDF versus 2.5 for binary AWM. This is source-level wiring evidence, not an authenticated runtime audit of the deployed service or database dataset.

`recommenders/tfidf.py::score_texts` uses character 2–3-gram TF-IDF and cosine over stored detailed category, food type, menu, activity type and description summary. This text scorer does not consume the ten numeric survey ratings. `taste_query` reads explicit positive taste keywords; avoid keywords are not positive query text. Blank user text yields zero similarities, regardless of high survey ratings. The API request includes numeric ratings, exclusions and optional taste keywords. `api/app/scores.py::to_fit` maps numeric ranges for display; it does not empirically calibrate satisfaction.

`pipeline.py::prepare_group` enforces supplied eligible IDs and excluded IDs before candidate generation. The database adapter resolves supplied exclusion rules. `average_without_misery.py` rejects a POI when any member falls below its configured threshold, with no relaxation fallback. `gfar.py` converts each member's own ranking to normalized Borda relevance and greedily updates uncovered relevance. It does not average preferences into one profile. It can return zero-gain items once coverage gains are exhausted, and deterministic ties can strongly affect all-neutral profiles.

The exact historical weighted-sum implementation asserted in the prompt is not the current binary scorer. Candidate A is therefore an explicitly labeled reconstruction, `dot(r,x)/sum(r)`, with a 0.25 threshold diagnostic. It is not represented as an exact recovered production baseline. The currently callable TF-IDF scorer is also tested directly, both with blank actual-input text and a declared positive-keyword control derived from synthetic ratings.

## Data readiness

| Dataset | Rows / unique IDs | Source-backed nonconflicting description rows | Mean description chars |
| --- | --- | --- | --- |
| travelsync_final_places_v2.csv | 3570 / 3570 | 0 | N/A |
| travelsync_poi_metadata_enriched_v3.csv | 3570 / 3570 | 2249 | 50.8 |
| travelsync_demo40.csv | 40 / 40 | 34 | 53.6 |

The canonical count is **3,570**, not 3,591. It has 2,856 one-feature POIs (80.0%), 684 two-feature POIs (19.16%), 30 three-feature POIs (0.84%), and none with zero or more than three. Ten binary features form only **21 distinct signatures**. Name, category, category_name, coordinates and all ten features are complete. Canonical phone is 52.61% missing; image URL 89.24%; road address 31.90%; place URL 9.66%; fine TourAPI cat1–cat3 fields 87.37%. Full field-level missingness is in `results/field_missingness.csv`.

Canonical names are Korean-only-script by this heuristic for 3,472 records, mixed Hangul/Latin for 95, and Latin-only for 3. This is script detection, not linguistic identification. The canonical CSV has no narrative column. The v3 sidecar supplies 2,249 source-linked nonconflicting descriptions (63.00% of POIs), averaging 50.8 characters: 2,033 Korean-script and 216 mixed. The remaining 1,321 rows use name/category fallback. The sidecar has 3,022 PARTIAL, 522 VERIFIED, 17 NOT_FOUND and 9 CONFLICT row statuses. A source URL plus nonconflicting status establishes eligibility for this stored-text pilot; it does not independently verify every sentence, identity match, field provenance or current venue condition.

Of eligible descriptions, 1,191 are `STRUCTURED_MENU_FACTS` and 59 `OFFICIAL_MENU_FACTS`; they must not be described as rich venue narratives. Other bases are retained in `data_audit.json`. This run did not crawl sources or fabricate descriptions. It uses the v3 sidecar by exact POI ID and asserts that its feature vectors match the canonical file. Menu-derived text and earlier summaries can contain overlapping information and collection bias.

There are no repeated canonical IDs, but 58 extra rows share exact names and 14 share coordinates. These may include branches or co-located venues. A conservative same-coordinate-grid/name-similarity heuristic flagged 27 review pairs; it can miss duplicates across grid boundaries and is not a deduplication decision. No records were merged.

**Gate:** taxonomy-only experiments are feasible on the full catalogue. The existing sidecar now supports a source-backed descriptive pilot, with major provenance and coverage qualifications. Canonical-only embeddings may mostly re-express the ten features plus taxonomy/name associations; sidecar descriptions are additional stored information, but their relevance benefit requires human evidence.

## Phase 1: structured scoring findings

The active-feature mean returns exactly 3 for every POI for an all-neutral user, independent of feature count. In the reconstructed old weighted sum, the same user's score is 0.1, 0.2 or 0.3 for one, two or three active features. The normalized mean removes this mechanical feature-count penalty. The figure `results/feature_count_bias.svg` and `structured_by_feature_count.csv` contain the executed check.

The formula still has limits. A POI tagged only with a rating-5 feature scores 5, while adding a rating-3 feature lowers it to 4; incomplete narrow tagging can therefore be rewarded. A rating-5 and rating-1 combination averages to 3 and hides an active dislike. The dislike variant subtracts lambda times `max(0,3-min_active_rating)` and clips to [1,5]; lambda 0, 0.25, 0.5 and 1 all ran, with no optimum selected. The same [5,1] example scores 2 at lambda 0.5. High-across-the-board survey users receive high feature scores by construction. Neither variant proves actual satisfaction or makes exclusions unnecessary.

## Phase 2: semantic models and ablations

MiniLM revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` uses mean pooling, normalized 384-dimensional embeddings and max length 128. E5-small revision `614241f622f53c4eeff9890bdc4f31cfecc418b3` uses normalized 384-dimensional embeddings and max length 512. No tested document exceeded either model's configured token limit. Both support Korean in their model cards. MiniLM has Apache-2.0 licensing; E5-small has MIT licensing. E5 receives `query: ` interest labels and `passage: ` POI text, including Korean inputs, following its input contract. Both measured 117,653,760 parameters and approximately 493–494 MB of local snapshot files. Model-card sources: [MiniLM](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), [E5-small](https://huggingface.co/intfloat/multilingual-e5-small).

POI representations are name + category, name + category + the actual active feature labels, and name + category + source-linked stored summary where eligible. The last arm falls back to name/category when missing. It does not append fabricated attributes. The two representations are compared on the *same* described subset and separately on the missing-description subset.

For interest k, positive weight is `max(r_k-3,0)` and negative weight is `max(3-r_k,0)`. Neutral ratings contribute zero. Weighted profiles average individual interest cosines using those weights, then subtract beta times the negative component. Centroid profiles separately form normalized positive and negative interest centroids before cosine comparison. Beta 0 is the positive-only ablation; 0.5 and 1 include negative evidence. There is no natural-language negation prompt whose handling is assumed reliable.

All-neutral profiles produce flat zero semantic relevance, a lack of directional evidence rather than universal dislike. No-positive profiles use only the negative penalty (or all ties for beta 0). Numeric ratings remain in the input and determine weights; however, component normalization discards common intensity scaling: all-4 and all-5 can induce the same semantic ranking, and all-1/all-2 negative profiles can do likewise. This is a limitation of the representation, not rating calibration. Ten coarse numeric interests cannot express venue-specific tastes not present in the survey.

| Scorer (description arm for models) | Relevance range | Mean distinct scores per profile | Food +1 top-10 overlap |
| --- | --- | --- | --- |
| feature | 1.000 to 5.000 | 6.6 | 0.856 |
| dislike_0.5 | 1.000 to 5.000 | 6.4 | 0.856 |
| paraphrase-multilingual-MiniLM-L12-v2 | -0.535 to 0.509 | 3330.6 | 0.663 |
| multilingual-e5-small | -0.807 to 0.807 | 3331.3 | 0.819 |

The structured scorer has essentially zero within-signature variance. MiniLM's corresponding mean within-signature standard deviation is 0.0401 and E5's is 0.00729 in the weighted beta-1 description arm. This proves ranking distinctions among identically tagged POIs, not correct distinctions. Relevance magnitudes across models have no common satisfaction interpretation. E5's high positive cosine baseline is particularly visible in the separate positive/negative components; their subtraction is a ranking heuristic, not a probability.

| Model | Subset | POIs | Mean absolute score change from name/category | Top-10 overlap |
| --- | --- | --- | --- | --- |
| paraphrase-multilingual-MiniLM-L12-v2 | described | 2249 | 0.07793 | 0.237 |
| paraphrase-multilingual-MiniLM-L12-v2 | missing_description | 1321 | 0.00000 | 1.000 |
| multilingual-e5-small | described | 2249 | 0.01029 | 0.319 |
| multilingual-e5-small | missing_description | 1321 | 0.00000 | 1.000 |

Descriptions materially change rankings on the described subset while leaving the missing-description subset's top ten unchanged. Tiny nonzero fallback score differences come from floating-point batch padding/inference, not invented text. These are ablation diagnostics, not evidence of improved NDCG. All beta/profile/representation results remain available, including unfavorable or indistinguishable settings.

## Cost and deployment

CPU-only, four Torch threads, batch size 32. Each 3,570-by-384 float32 POI matrix occupies 5,483,520 bytes (5.23 MiB), excluding index and model storage. Ten interest embeddings can also be cached because the labels do not change. This makes online profile construction a weighted combination rather than repeated Transformer inference.

| Model | Text representation | First-pass catalogue encoding seconds | Warm 10-interest median ms | Process working set MiB |
| --- | --- | --- | --- | --- |
| paraphrase-multilingual-MiniLM-L12-v2 | name_category | 12.18 | 20.6 | 808 |
| paraphrase-multilingual-MiniLM-L12-v2 | features | 14.91 | 26.8 | 828 |
| paraphrase-multilingual-MiniLM-L12-v2 | description | 30.11 | 20.9 | 857 |
| multilingual-e5-small | name_category | 14.60 | 27.7 | 1223 |
| multilingual-e5-small | features | 17.62 | 48.1 | 1231 |
| multilingual-e5-small | description | 32.82 | 25.9 | 1265 |

These are single-machine diagnostic measurements, not a deployment SLA. First-pass means this experiment's embedding cache miss, not an OS-cold model load. Warm interest timings are medians of three batches. Memory includes Python, Torch and retained allocators from earlier model loads; E5 and MiniLM memory values are not independent model-only memory measurements. Peak working set in the combined process reached approximately 1,323 MiB. Model load was about 0.8 seconds in this environment. Download time is excluded. Cold results were preserved separately before cached reproducibility runs; current `model_benchmarks.csv` records cache reads. Embedding generation is entirely separate from aggregation.

## Phase 3: diagnostic hybrid and group comparisons

The independent-quality gate is **not met**. To complete the requested technical comparisons, hybrid and group arms ran as diagnostics only. Percentile normalization uses average tied ranks on [0,1]; min-max uses the candidate-specific observed range. Constant rows map to 0.5. Neither is calibrated satisfaction. Alpha 0/0.25/0.5/0.75/1 all ran for weighted beta-1 profiles. No alpha, beta, lambda or checkpoint was selected using proxy utility.

At alpha 0.5, percentile/min-max individual top-10 overlap ranges from 0.9875 to 1.0 across the six model/representation combinations; at alpha 0.75 it falls as low as 0.95625. These are candidate-dependent ranking transformations; they can change choices and are not interchangeable scales. All rows are in `normalization_sensitivity.csv`. A held-out study must freeze normalization in advance.

| Candidate | Individual scoring | Aggregation | Executed status |
| --- | --- | --- | --- |
| A | Historical weighted-sum reconstruction | AWM at 0.25 diagnostic | Not recovered active binary code; unvalidated-scale survival diagnostic only |
| B | Active-feature mean | GFAR | Executed; preferred simple baseline, no quality superiority claim |
| C | MiniLM and E5 semantic relevance | GFAR | Executed; labels needed |
| D | Structured + normalized semantic | GFAR | Diagnostic grid executed; adoption not justified |
| E | Dislike-aware mean | GFAR | Lambda grid executed; no optimal penalty selected |

The same individual matrices were also sent to simple average and GFAR. AWM ran at 2.5 only for the survey-feature mean and dislike variants, where the threshold denotes a modeled survey-feature preference fit. Even there it is not empirically validated misery. The adapter refuses raw semantic, cosine, legacy or normalized-rank scales. No old 0.25 threshold is applied to SentenceTransformer scores.

Current TF-IDF with blank queries returns zero everywhere. Its 0.25 survival diagnostic is zero for all four groups. The declared positive-keyword TF-IDF control also yields zero surviving candidates for all four groups; many positive queries have near-zero scores. These are incompatible-scale/input-evidence failures, not observed universal dissatisfaction. The reconstructed weighted baseline has surviving counts 30, 26, 30 and 0 across the four synthetic groups. These separate scale diagnostics use the unfiltered 3,570-row catalogue and are not quality comparisons with the constrained group runs.

Structured AWM has surviving/returned counts [(0, 0), (715, 10), (1132, 10), (1, 1)]. The first empty pool has an all-1 user and is expected threshold exhaustion. The last group has only one jointly threshold-qualified POI; it is reported as a short pool, never filled by relaxing constraints or silently assigning zero list utility. GFAR retains 3,568 eligible candidates in each case and returns ten, which does not guarantee satisfaction for the all-1 member.

Below, every number is the mean of **the same structured feature scorer on selected POIs**, not independent member satisfaction. Using this proxy favors methods aligned with structured features. Group 0 is deliberately degenerate. Full results include hybrids, every lambda and simple averages in `group_comparisons.csv`; ranked IDs and every member's native and proxy score are in `group_recommendations.csv`.

| Scorer + GFAR | Group | Returned | Per-member feature proxy means | Worst-member feature proxy | Proxy disparity |
| --- | --- | --- | --- | --- | --- |
| feature | 0 | 10 | [3.0, 4.0, 1.0, 5.0] | 1.0 | 4.0 |
| feature | 1 | 10 | [4.0, 3.0, 3.5, 3.5] | 3.0 | 1.0 |
| feature | 2 | 10 | [4.2, 2.6, 2.7, 4.0] | 2.6 | 1.6 |
| feature | 3 | 10 | [3.8, 3.8, 2.8, 4.4] | 2.8 | 1.6000000000000005 |
| dislike_0.5 | 0 | 10 | [3.0, 4.0, 1.0, 5.0] | 1.0 | 4.0 |
| dislike_0.5 | 1 | 10 | [4.0, 3.0, 3.5, 3.5] | 3.0 | 1.0 |
| dislike_0.5 | 2 | 10 | [4.2, 2.6, 2.7, 4.0] | 2.6 | 1.6 |
| dislike_0.5 | 3 | 10 | [3.8, 3.8, 2.8, 4.4] | 2.8 | 1.6000000000000005 |
| paraphrase-multilingual-MiniLM-L12-v2 | 0 | 10 | [3.0, 4.0, 1.0, 5.0] | 1.0 | 4.0 |
| paraphrase-multilingual-MiniLM-L12-v2 | 1 | 10 | [4.2, 3.3, 3.2, 3.0] | 3.0 | 1.2000000000000002 |
| paraphrase-multilingual-MiniLM-L12-v2 | 2 | 10 | [4.433333333333333, 3.7833333333333328, 3.5666666666666673, 3.766666666666667] | 3.5666666666666673 | 0.8666666666666654 |
| paraphrase-multilingual-MiniLM-L12-v2 | 3 | 10 | [2.45, 4.15, 3.1, 3.6] | 2.45 | 1.7000000000000002 |
| multilingual-e5-small | 0 | 10 | [3.0, 4.0, 1.0, 5.0] | 1.0 | 4.0 |
| multilingual-e5-small | 1 | 10 | [3.75, 3.8, 2.9, 3.35] | 2.9 | 0.8999999999999999 |
| multilingual-e5-small | 2 | 10 | [4.5, 3.85, 3.6, 3.5] | 3.5 | 1.0 |
| multilingual-e5-small | 3 | 10 | [2.5, 4.5, 3.1, 3.5] | 2.5 | 2.0 |

GFAR member representation is recorded as whether the output includes a member's deterministic top-20 item. Tied lists can make that indicator misleading; it is a ranking coverage diagnostic, not fairness or acceptance. Feature diversity counts represented feature dimensions, not geographic or experiential diversity. GFAR's own rank coverage is also not worst-member satisfaction. No group acceptance, actual fairness improvement, NDCG, precision or user-satisfaction superiority is asserted.

## Verification and remaining evidence

`results/tests.txt`: **68 tests passed and 21 subtests passed**, including 19 study-specific test cases plus the existing root suite. One pre-existing importlib-metadata deprecation warning remains. Tests cover the nine requested regressions, weighted rating effects, dislike penalties, hybrid endpoints, explicit scale/range guards and tie-expected human evaluation metrics. No database integration tests or web end-to-end session were run because no production path was changed.

`verify.py --rerun` reran both cached models and all configurations offline. Fifteen deterministic data/result/packet/figure artifacts were byte-identical. 235 pre-existing inputs/tracked files passed before/after SHA256 checks, including the pre-existing untracked candidate CSV. Model caches are ignored; production dependencies and lockfiles are unchanged. Tests and reproducibility were actually executed, not merely specified.

Existing root tests and historical experiment/annotation tooling were inspected. `notebooks/experiments/human_evaluation_phase1_20261029.py` explicitly treats two earlier sheets as one blinded person's two rating sets. Their different frozen free-text scenarios and rubric are not aligned labels for these sixteen numeric profiles. They were not reused as two independent raters, a consensus standard or a held-out validation set. No independent judgments aligned to the present experiment were established.

`HUMAN_EVALUATION.md`, a disjoint 24-development/24-held-out POI labeling template and `evaluate_labels.py` are ready. Collect actual participant ratings and group judgments before selecting a winner. The evaluator implements NDCG@5/@10, willingness@K and ranking correlation on a complete judged packet, with explicit insufficient-evidence handling. It did not run on invented human labels. Satisfaction calibration, real group acceptance and confirmatory significance estimates require additional evidence.

## Recommended architecture and answer

Keep hard constraints as an explicit eligibility stage, independent of scoring. Preserve one score/ranking per member. Use active-feature mean as the reference individual scorer, GFAR as the primary group aggregator, and scale-checked AWM as a sensitivity baseline. Return unclassified/exhaustion causes and distinguish survey preference fit, semantic relevance and normalized rank scores in the contract. Do not label similarity as satisfaction or acceptance probability. Keep candidate generation, scoring, aggregation, embeddings and itinerary optimization modular.

The study establishes that stored text can add distinctions beyond ten binary features and that pretrained inference is locally feasible. It does **not** establish that those distinctions are useful enough to justify adding SentenceTransformer to production. Retain the simpler scorer pending a blinded, held-out comparison using real numeric profiles and credible POI descriptions. No SRS/SDD revision is justified as an empirically superior semantic architecture yet; explicit score semantics and the removal of cosine-based misery assumptions are justified design clarifications. Any future integration should first validate descriptive provenance, missing-data behavior and human relevance, then compare the additional benefit with its deployment cost.
