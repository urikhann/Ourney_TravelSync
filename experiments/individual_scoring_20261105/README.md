# Individual scoring study

Everything here is isolated from production code, schema and official CSVs. Read `REPORT.md` for the decision and `results/` for executed evidence. The model and embedding caches are ignored by Git.

From the repository root:

```powershell
uv sync --locked --extra semantic
uv run --no-sync python experiments/individual_scoring_20261105/run.py
uv run --no-sync pytest experiments/individual_scoring_20261105/test_scoring.py -q
uv run --no-sync python experiments/individual_scoring_20261105/verify.py --rerun
uv run --no-sync python experiments/individual_scoring_20261105/report.py
```

The runner uses offline CPU inference with four Torch threads. It requires the
immutable model snapshots and cache paths specified in `config.json`. Package
versions are captured in `results/manifest.json`. On a fresh checkout, prepare
those public checkpoints explicitly before running inference; no project data
needs to leave the machine.

`fetch_models.py` downloads only the pinned public checkpoints if they are missing. Run it explicitly with the semantic Python environment when preparing a fresh checkout. No live inference API is used. `scoring.py` separates survey feature scores, uncalibrated semantic relevance, distribution-normalized hybrids and aggregation. AWM accepts only explicitly declared survey-feature 1–5 scores in the safe adapter. That scale is a preference-fit model, not empirical satisfaction calibration. `run.py` first audits local data, then runs embeddings, then technical hybrid/group comparisons. Hybrid/group results are diagnostics: the independent-quality gate is not met.

Input hashes, model revisions, seed, feature order, synthetic profiles, parameters, top-ranked POI IDs, per-member scores, distributions, coverage, survival counts, inference benchmarks and package versions are saved. Repeat runs overwrite only this study's output files; caches avoid repeated embedding generation. Timings/cache-hit flags change on repeat runs. Integrity checks hash protected inputs before and after execution. `HUMAN_EVALUATION.md` describes the separate collection required for quality claims; `evaluate_labels.py` consumes actual held-out judgments only.
