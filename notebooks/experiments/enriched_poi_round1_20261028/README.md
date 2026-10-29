# Enriched POI round 1 — 2026-10-28

Isolated, offline preference-matching feasibility experiment. No app, production scorer, official dataset or database changes. See [REPORT.md](REPORT.md) for the executed results. All paths below are relative to the repository root and use PowerShell.

## Saved artifacts

`data/generated/evaluation/enriched_poi_round1_20261028/` contains:

- `inputs/`: byte-preserved copies of all four supplied files; originals remain untouched.
- `manifest.json`: source hashes/versions, revision, environment and protected tracked-file hashes, plus pre-ranking freeze hashes.
- `config.json`, `scenarios.json`: frozen method rules, 12 scenarios, preference vectors, rationale, evidence IDs and assumptions.
- `eligible_candidates.csv`, `feature_groups.csv`, `candidate_audit.csv`: all group memberships, the 19 shared candidates, all 21 exclusions and reasons.
- `field_provenance.csv`, `missing_values.csv`, `conflicts.csv`: field-level source flattening, original missing tokens, unresolved issues.
- `results/`: 152 primary scores/ranks with exact tied bands, cleaned inputs, lexical models/contributions, semantic metadata/embeddings, diagnostics and sensitivity outputs.
- `results/annotation_R1.csv`, `annotation_R2.csv`, `ANNOTATION_RUBRIC.md`: blinded, independently randomized sheets and Korean rubric. **Only distribute these three files to raters**, not scores or the report. Each sheet has 38 pairs with untouched blank rating fields.
- `results/relevance_metrics.json`: explicitly not yet evaluated.
- `repeat_validation/`: independently rerun outputs on the same frozen inputs.
- `verification.json`: artifact hashes, complete rerun byte comparisons and verification evidence.

Generated data, model weights and the isolated environment are ignored by Git. The code/report directory is the reviewable implementation. Retain/archive the generated experiment directory alongside it; Git alone does not contain the supplied data. No earlier experiment is overwritten.

## Rerun existing frozen experiment

Methods A–C require the repository environment (NumPy only). No network is needed:

```powershell
$E = 'notebooks/experiments/enriched_poi_round1_20261028'
.venv/Scripts/python.exe "$E/run.py" --results-name rerun_abc_01
```

Choose an unused results name on every run. This validates source/configuration hashes and protected code/data before running. B/C IDF is fit on the shared 19-place corpus separately for each text variant, once across all scenarios, with queries excluded. Missing values never become facts or invented descriptions. The baseline imports the actual repository scorer.

For all four methods, use the supplied experiment-only environment, or create it when absent and install its pinned snapshot. Setup requires public package/model downloads; inference is entirely local, uses no credentials and forces offline mode:

```powershell
$E = 'notebooks/experiments/enriched_poi_round1_20261028'
if (!(Test-Path "$E/.semantic-venv/Scripts/python.exe")) {
    uv --cache-dir .uv-cache venv "$E/.semantic-venv" --python .venv/Scripts/python.exe
}
uv --cache-dir .uv-cache pip install --python "$E/.semantic-venv/Scripts/python.exe" -r "$E/requirements-semantic.txt"
& "$E/.semantic-venv/Scripts/python.exe" "$E/fetch_model.py"
& "$E/.semantic-venv/Scripts/python.exe" "$E/run.py" --semantic --results-name rerun_abcd_01
.venv/Scripts/python.exe -m unittest discover -s $E -p test_experiment.py -v
```

Recorded execution used Python 3.13.15 on Windows, CPU float32, torch 2.14.1, sentence-transformers 5.1.2, transformers 4.57.1, NumPy 2.5.4. The environment was created with system-site-packages enabled; the pinned snapshot contains the installed embedding dependencies. The code explicitly imports the checked-out scorer from `src/`, rather than relying on a separately installed package. Full cross-process outputs were byte identical here; other BLAS/hardware/library versions may alter floating point values. Record that change rather than hiding it. A semantic run requires the complete pinned local model snapshot. A failed/partial result directory must be preserved or given a different subsequent run name.

## Freeze a new experiment

Use this only to produce a new audited copy of the same scenario design, before scoring. Freeze never accepts an existing output directory:

```powershell
$E = 'notebooks/experiments/enriched_poi_round1_20261028'
.venv/Scripts/python.exe "$E/prepare.py" --source-dir 'D:/Backup/Documents/KakaoTalk Downloads' --out 'data/generated/evaluation/enriched_poi_round1_new_freeze'
.venv/Scripts/python.exe "$E/run.py" --out 'data/generated/evaluation/enriched_poi_round1_new_freeze' --results-name results
```

Changing scenarios, exclusions or lexical rules requires a new experiment/version. Do not rewrite the frozen JSON to improve a method's observed results. `manifest.json` captures the freeze time and code hash; `run_manifest.json` binds scoring code and artifacts to that freeze.

## After two independent human ratings

Retain the blank templates. Collect each completed CSV separately, with no method-score access or collaboration between raters. Grade 0–3 only when evidence is sufficient; otherwise mark YES and leave grade blank. No label is generated by this experiment. Document actual collection in a separate JSON:

```json
{
  "origin": "independent_human",
  "independent": true,
  "method_blinded": true,
  "rater_ids": ["R1", "R2"],
  "collection_date": "ACTUAL_DATE",
  "collection_description": "Describe actual independent human collection and blinding."
}
```

The JSON is a collector declaration, not automatic proof of independent origin. The evaluator validates required declarations, identities, unchanged prompts/evidence, grades and missingness; review the actual collection process before interpreting metrics. Do not use model-generated grades or tune on these final labels.

```powershell
.venv/Scripts/python.exe "$E/evaluate.py" --rater1 'path/to/completed_R1.csv' --rater2 'path/to/completed_R2.csv' --human-evidence 'path/to/collection_evidence.json' --destination 'data/generated/evaluation/enriched_poi_round1_20261028/human_evaluation_01'
```

The evaluator reports each rater and their mean separately, with per-scenario exclusions. NDCG uses gain `2**grade - 1`, discount `1/log2(rank+1)`, and `k=min(3, group size)`. Primary NDCG is expected DCG under random ordering within each exact score band, including bands crossing k; ID-fallback NDCG is also exposed to show ordering artifacts. A scenario with any insufficient evidence is excluded from NDCG for that label set; zero ideal DCG is N/A. Pairwise agreement uses judged pairs with unequal human grades: matching strict order=1, model tie=0.5, reversal=0; no applicable pairs=N/A. Missing human grades never become zero, and no candidates are silently dropped from NDCG. The mean grade exists only where both independent raters marked evidence sufficient. Exact rater agreement and mean absolute disagreement are included for interpretation.

## Validation commands

```powershell
.venv/Scripts/python.exe -m unittest discover -s $E -p test_experiment.py -v
.venv/Scripts/python.exe -m pytest tests -q --basetemp .uv-cache/pytest-enriched-round1-new-run -p no:cacheprovider
.venv/Scripts/python.exe "$E/verify.py"
```

The tests use synthetic grades only to verify metric mathematics in memory; those values are not exported as human labels or evaluation results. No database integration tests are run. The first default-temp pytest attempt encountered an operating-system permission error during temporary-directory cleanup; the repository tests completed successfully with a fresh workspace temporary directory.
