# Experiment 2 — POI enrichment coverage and ranking robustness

Isolated experiment; no production changes or GFAR integration. Read `source_review.json` for the bounded source follow-up and `robustness_report.md` in generated output for executed findings.

From the repository root (PowerShell), use the existing offline semantic environment:

```powershell
& 'notebooks/experiments/enriched_poi_round1_20261028/.semantic-venv/Scripts/python.exe' notebooks/experiments/poi_robustness_20261029/run_robustness.py --out data/generated/evaluation/poi_robustness_20261029_rerun_01
.venv/Scripts/python.exe -m unittest discover -s notebooks/experiments/poi_robustness_20261029 -p test_robustness.py -v
```

Choose a new output path for every run. `--no-semantic` supports A/B/C-only execution in the repository environment. No dependency installation, model download, training, database access or production writes occur. The new B/C corpora are fitted once per domain, with no refitting after masking. The annotated sensitivity control restores the original frozen model and candidate groups instead of applying partial labels to new pools.

The original 40-POI input, canonical dataset, original experiment and Phase 1 artifacts are protected by before/after hashes. The sole new summary resides in a separate overlay. R1/R2 in annotated outputs are two sheets from one human, not independent raters. No new candidate-pool relevance labels are generated.
