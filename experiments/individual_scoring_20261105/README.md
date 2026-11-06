# Individual scoring study, 2026-11-05

Everything here is isolated from production code, schema and official CSVs. Read `REPORT.md` for the decision and `results/` for executed evidence. The model and embedding caches are ignored by Git.

From the repository root, use the existing semantic environment (the root environment lacks sentence-transformers):

```powershell
notebooks/experiments/enriched_poi_round1_20261028/.semantic-venv/Scripts/python.exe experiments/individual_scoring_20261105/run.py
.venv/Scripts/python.exe -m pytest experiments/individual_scoring_20261105/test_scoring.py -q -p no:cacheprovider
notebooks/experiments/enriched_poi_round1_20261028/.semantic-venv/Scripts/python.exe experiments/individual_scoring_20261105/verify.py --rerun
.venv/Scripts/python.exe experiments/individual_scoring_20261105/report.py
```

The semantic environment's pinned package snapshot is `notebooks/experiments/enriched_poi_round1_20261028/requirements-semantic.txt`; the actual environment versions are captured in `results/manifest.json`. Production dependencies and lockfiles are unchanged. The runner is offline and CPU-only with four Torch threads. It needs the two immutable model snapshots listed in `config.json`. MiniLM reuses the previous experiment's local cache; E5 uses this experiment's `.model-cache`. For a fresh machine, download those public model snapshots explicitly using Hugging Face `snapshot_download` with the exact revision and `token=False`, permitting JSON, TXT, safetensors, tokenizer model, README and pooling files while excluding ONNX/OpenVINO duplicates. No project data needs to leave the machine.

`fetch_models.py` downloads only the pinned public checkpoints if they are missing. Run it explicitly with the semantic Python environment when preparing a fresh checkout. No live inference API is used. `scoring.py` separates survey feature scores, uncalibrated semantic relevance, distribution-normalized hybrids and aggregation. AWM accepts only explicitly declared survey-feature 1–5 scores in the safe adapter. That scale is a preference-fit model, not empirical satisfaction calibration. `run.py` first audits local data, then runs embeddings, then technical hybrid/group comparisons. Hybrid/group results are diagnostics: the independent-quality gate is not met.

Input hashes, model revisions, seed, feature order, synthetic profiles, parameters, top-ranked POI IDs, per-member scores, distributions, coverage, survival counts, inference benchmarks and package versions are saved. Repeat runs overwrite only this study's output files; caches avoid repeated embedding generation. Timings/cache-hit flags change on repeat runs. Integrity checks cover all pre-existing tracked files and the pre-existing untracked candidate CSV. `HUMAN_EVALUATION.md` describes the separate collection required for quality claims; `evaluate_labels.py` consumes actual held-out judgments only.
