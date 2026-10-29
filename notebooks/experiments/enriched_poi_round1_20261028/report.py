"""Regenerate the human-readable round-one report from frozen, verified artifacts."""
from collections import Counter, defaultdict
import json
from pathlib import Path
from prepare import DEFAULT_OUT, HERE, read_csv
from run import verify_frozen


def report(out=DEFAULT_OUT):
    manifest = verify_frozen(out)
    results = out / "results"
    diagnostics = read_csv(results / "diagnostics.csv")
    scores = read_csv(results / "scores_ranks.csv")
    audit = read_csv(out / "candidate_audit.csv")
    eligible = read_csv(out / "eligible_candidates.csv")
    groups = read_csv(out / "feature_groups.csv")
    scenarios = json.loads((out / "scenarios.json").read_text(encoding="utf-8"))
    semantic = json.loads((results / "semantic_metadata.json").read_text(encoding="utf-8"))
    masks = read_csv(results / "description_mask_sensitivity.csv")
    differences = read_csv(results / "ranking_differences.csv")
    verification = json.loads((out / "verification.json").read_text(encoding="utf-8"))
    assert verification["status"] == "PASS"
    table = []
    for method in "ABCD":
        ds = [r for r in diagnostics if r["method"] == method]
        pair_total = sum(int(r["candidates"]) * (int(r["candidates"]) - 1) // 2 for r in ds)
        tied = round(sum(float(r["tied_pair_fraction"]) * int(r["candidates"]) * (int(r["candidates"]) - 1) / 2 for r in ds))
        distinct = sum(int(r["distinct_score_bands"]) == int(r["candidates"]) for r in ds)
        table.append(f"| {method} | {tied}/{pair_total} ({100*tied/pair_total:.1f}%) | {distinct}/12 |")
    group_table = []
    for g in groups:
        if int(g["described_count"]) < 2:
            continue
        names = ", ".join(r["place_name"] for r in eligible if r["group_id"] == g["group_id"])
        if not names:
            names = "None: 타보로 category conflict; 컴포즈커피 becomes singleton"
        group_table.append(f"| `{g['group_id']}` | {g['described_count']} | {g['primary_count']} | {names} |")
    missing_names = ", ".join(r["place_name"] for r in audit if r["has_description"] == "False")
    conflicts = read_csv(out / "conflicts.csv")
    conflict_names = ", ".join(r["place_name"] for r in audit if r["detail_verification_status"] == "CONFLICT")
    bc_changed = sum(int(r["changed_fallback_positions"]) > 0 for r in differences if r["method_left"] == "B" and r["method_right"] == "C")
    text = f"""# TravelSync enriched-POI experiment — round 1

Executed 2026-10-28 (Asia/Seoul). **All four methods ran. The enriched information can produce different scores within original-feature ties; recommendation relevance remains unevaluated. No winning method or production change is justified.**

## Scope and input audit

This is a curated feasibility study of 40 deliberately selected demo places, not a citywide sample or a real-user satisfaction study. The four supplied files were copied byte-for-byte into `inputs/`, hashed, and audited. The canonical v2 dataset was read for identity/feature checks only. Git revision: `{manifest['git_revision']}`. Freeze timestamp: `{manifest['frozen_at_utc']}`. Exact hashes and package inventories are in `manifest.json` and `results/run_manifest.json`.

Verified: 40 unique IDs; valid nonzero ten-dimensional binary vectors; all names, addresses, coordinates and feature values match the official dataset. The 40 places have **19 distinct feature vectors, 34 supplied descriptions, and seven groups containing at least two described places**. The missing-information input has 216 records. All 34 description_raw values are explicitly CORE_FACTS_PARAPHRASE; neither raw nor summary is a verbatim source document.

Every populated ranking field has at least one field-level provenance entry with a source and check date; none failed that structural check. This checks the supplied provenance, not the truth or current availability of each place. Ranking-field provenance contains 51 FULL_TEXT, 29 SEARCH_EXCERPT, 30 INHERITED_INPUT and 4 INHERITED_MAP entries (field-source records can overlap; these are not counts of independent places). Demo stay-time assumptions are excluded from ranking. `field_provenance.csv` retains every source record and limitation.

Six missing descriptions: {missing_names}.

Seven source CONFLICT flags: {conflict_names}. The primary policy excludes 이태석신부생가 (unresolved birthplace/memorial/address scope) and 타보로 (original café features versus restaurant attributes). Other conflicts concern operating conditions and remain recorded; they do not automatically exclude a preference-matching candidate. 윤산 retains only the supplied mountain-wide description; its uncertain starting point is not resolved here. Missing-description places with boundary/identity concerns remain excluded. `conflicts.csv` and `candidate_audit.csv` retain the details.

The shared primary subset is **19 places in six groups**. Exclusions total 21: six missing descriptions, two unresolved identity/attribute conflicts, and 13 candidates left in groups with fewer than two eligible members. Prices and hours are never eligibility requirements. All exclusions and exact IDs are in `candidate_audit.csv`; all eligible IDs are in `eligible_candidates.csv`.

Feature signature order: food, cafe, nature, history, art, shopping, activity, entertainment, relax, festival.

| Original feature signature | Described places | Primary places | Primary membership |
| --- | ---: | ---: | --- |
{chr(10).join(group_table)}

NULL/blank are preserved as **unspecified unknown-or-not-applicable**, because the source uses NULL for both; the experiment cannot recover that distinction. Explicit UNKNOWN is kept distinct from explicit N/A/NOT_APPLICABLE (the latter tokens do not occur here). `missing_values.csv` preserves 1,473 unspecified-null and 86 explicit-unknown cells, not unique places. Omitted text fields become empty text only in model inputs; they never become negative attributes, zero prices or unrestricted hours.

## Frozen comparisons

Twelve synthetic Korean scenarios were frozen before comparative scoring: two each for food, making activities, camping/sports facilities, forest/beach experience, park classification/vegetation, and historical exhibitions/temples. `scenarios.json` contains stable IDs, full Korean statements, feature vectors, mapping rationale, evidence-place IDs and assumptions. Author-defined projections assign 5 to this candidate group's active categories and neutral 3 elsewhere. Thus A happens to score every primary pair at 5; identical vectors would tie for **any** valid preference vector. The mapping is not an observed survey response. Scenarios were not rewritten after observing rankings.

The Korean statements add user-side specificity absent from the original ten-category survey. Any later measured difference would combine richer user inputs, richer place information and method behavior; this design does not isolate a model-only effect.

- **A:** imports `score_individuals` from the actual production scorer. Formula `sum_j preference_j * binary_feature_j / sum_j binary_feature_j`, on the original 1–5 scale. No alternative normalization from a design document is used.
- **B:** cosine of character 2/3-gram TF-IDF over detailed_category, food_type, representative_menu and activity_type.
- **C:** exactly B's lexical procedure, adding description_summary. Each variant fits its vocabulary/IDF separately on the same 19 primary documents, excluding queries, once for all scenarios. Therefore B–C differences include vocabulary, IDF and length-normalization effects. `lexical_models.json` and `lexical_contributions.csv` expose the calculation.
- **D:** local CPU inference of `{semantic['id']}` at revision `{semantic['revision']}`. Its official [model card](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) lists Apache-2.0 and sentence similarity/search usage; official [Sentence Transformers documentation](https://sbert.net/docs/sentence_transformer/pretrained_models.html) includes Korean among its supported languages. Mask-aware mean pooling produces 384 dimensions; L2-normalized embeddings use dot product as cosine. Inputs are truncated on the right at 128 total tokens including specials. **{semantic['truncated_input_count']} of 52 inputs were truncated** (40 documents plus 12 scenarios). CPU float32, eval mode, one torch thread, no prompts; exact model-file hashes, token lengths and embedding order are saved. Cosine is not a satisfaction probability or a calibrated 1–5 preference score.

B–D use the same cleaned Korean query. Text cleaning is NFKC, lowercase, punctuation to spaces, whitespace collapse; no translation, synonym expansion or inferred attributes. Only the listed fields enter text. candidate_type, selection_reason, A/B/C/D model scores, compromise ranks, names, addresses and provenance/selection metadata never enter model text. Names appear only in review/annotation outputs. Original text remains separate in the input snapshot. usage_conditions and target restrictions appear only as neutral annotation context; they are not ranking terms or feasibility filters.

All methods rank exactly the same candidates per primary scenario. Exact native-score equality defines visible score bands; descending score then ascending full place_id string is the fallback. ID order changes cannot establish relevance. No opening hours, prices or event dates are used to imply that any itinerary is feasible.

## Observed diagnostics

There are **38 scenario–place pairs and 152 method-score rows**. Across these scenarios there are 58 within-scenario candidate-pair comparisons; the same small groups recur, so these are not independent human observations.

| Method | Exactly tied pairs / 58 | Scenarios with all scores distinct |
| --- | ---: | ---: |
{chr(10).join(table)}

These are separation diagnostics, **not accuracy, relevance or satisfaction improvements**. A's score is 5 throughout; the text similarities have different scales and are not comparable to A's magnitude.

C changes fallback positions relative to B in {bc_changed}/12 scenarios, with strict pair reversals in S05 and S08 as well as changes to tied bands. In S07 (편백/삼나무/숲 산책로), B has zero lexical overlap for all six candidates; C has nonzero scores for three and ranks 성지곡산림욕장 first. In S08, B leaves two beach candidates tied, C puts 부산 송도해수욕장 first, and D puts 광안리해수욕장 first. These differences need human judgments.

An unfavorable semantic result is retained: for S01's explicit 떡볶이 request, D scores 맛나감자탕 부산경남지사 0.56406 versus 빨간떡볶이 0.48206; B and C rank 빨간떡볶이 first. This is a source/query consistency concern, not an independently labeled error rate. It demonstrates why complete score separation cannot select D as the winner.

Missing-information sensitivity masks one primary candidate's description at a time while holding C's vocabulary/IDF and candidate set fixed: **{sum(int(r['changed_positions']) > 0 for r in masks)}/38 masks change positions; {sum(r['top1_changed'] == 'True' for r in masks)}/38 changes the top result** (S07, 성지곡산림욕장). Separate exploratory inclusion of all original candidates in the six tested groups yields 23 places and 184 score rows, with the same frozen lexical models. A's ID-fallback first result changes for S09/S10 when missing-description 신선대유원지 returns. D's first result changes for S11/S12 when the unresolved birthplace returns. This sensitivity deliberately includes excluded/missing-information rows and is not the shared primary evaluation; join candidate_audit.csv for their status. The excluded café group has no valid primary counterpart and is not evaluated through this sensitivity file.

## Human evaluation and validation

No independent scenario relevance labels were found in the supplied files or the repository's annotation/relevance search. Existing A/B/C/D scores are model outputs; feature-validation instructions and curation/manual approval do not supply relevance judgments. Neither the scenarios, rubric nor this report constitutes independent evidence.

Two independently shuffled, blinded CSV sheets contain the same 38 pairs, neutral source-supported place information, 0–3 relevance and insufficient-evidence fields. Both sets of human fields are blank. **NDCG and pairwise relevance agreement are NOT YET EVALUATED.** Give raters only their sheet and the Korean rubric, with no report, rankings, method identities or scores.

`evaluate.py` can be rerun after real annotations and collection provenance are supplied. It reports each rater and their mean, uses `k=min(3, actual group size)` (2 or 3 here), gain `2**grade-1`, log discounts, and tie-expected NDCG plus ID-fallback NDCG. Insufficient evidence excludes the complete scenario from NDCG; zero ideal gain is N/A. Pairwise agreement uses only unequal, sufficiently evidenced human grades: correct order=1, model tie=0.5, reversal=0, no applicable pair=N/A. Per-scenario exclusions remain visible. No final-label tuning is permitted.

Validation: **7 focused experiment tests passed; 40 repository tests passed, 2 skipped, 21 subtests passed.** The first pytest attempt hit a Windows temporary-directory cleanup permission error; rerunning with a workspace temporary directory completed successfully, with a nonfatal default-cache permission warning. The two complete inference runs are byte identical across **{verification['independent_rerun_files_compared']} result files**, including embeddings, score bands, sensitivity diagnostics and annotation sheets. Canary perturbations verify exclusion of selection metadata; shared identities, finite scores, exact binary ties and blank human fields pass. All four original files, the canonical dataset and {verification['tracked_file_count']} pre-existing tracked files retain their hashes. No database was accessed by experiment code. Project dependencies/lockfile and production/application files were not changed. Credential scanning found no matching secret patterns in deliverable text; no credentials are used for inference. See `verification.json`, the result manifests and README for exact rerun commands.

## Recommendation

**Continue to a small, independent human relevance evaluation of the six primary groups.** This data supports that limited review, with insufficient-evidence responses allowed; it does not establish relevance on the full 40 or the city. No winning method should be chosen yet. Preserve the frozen results and collect two blinded ratings before making a ranking-quality claim.

Specific food/activity/experience preferences are needed beyond the original survey to test these distinctions. Next collect naturally elicited user statements, separately from these source-authored scenarios, and expand source-verified descriptions for the six missing places. Resolve the birthplace and café/restaurant conflicts; improve attribute specificity only where sources support it. Sparse descriptions must not be filled by inference. Current access, event and operating conditions require a separate feasibility experiment.

A later AWM/GFAR experiment is justified by independently collected per-member relevance evidence, adequate inter-rater consistency/coverage, and repeatable gains on broader held-out scenarios over the binary tied baseline and lexical controls, with uncertainty and unfavorable cases retained. Predefine the split and comparison before using held-out labels. Use shared candidate pools and list lengths, inspect each member and worst-member utility, veto/empty/short-list rates, and missing-data effects. Validate any semantic-to-1–5 calibration on separate data before applying AWM's existing thresholds; cosine must not be inserted as a satisfaction score. Group experiments then need consented real group preferences and independent outcomes. This round alone supplies none of that validation.
"""
    (HERE / "REPORT.md").write_text(text, encoding="utf-8")
    (out / "REPORT.md").write_text(text, encoding="utf-8")
    print("Report regenerated from", out)


if __name__ == "__main__":
    report()
