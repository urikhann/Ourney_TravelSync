# Method C + GFAR offline experiment — 2026-10-30

The source data, profile matrices and fixed models come from `data/generated/evaluation/ai_metadata_20261030_verified/`. Do not replace that bundle with a different enrichment version. No document or application integration is changed.

From the repository root, choose an unused output directory:

```powershell
& 'notebooks/experiments/enriched_poi_round1_20261028/.semantic-venv/Scripts/python.exe' -m unittest discover -s notebooks/experiments/c_gfar_20261030 -p 'test_*.py' -v
& 'notebooks/experiments/enriched_poi_round1_20261028/.semantic-venv/Scripts/python.exe' notebooks/experiments/c_gfar_20261030/experiment.py --out data/generated/evaluation/c_gfar_20261030_rerun01
& 'notebooks/experiments/c_gfar_20261030/.plot-venv/Scripts/python.exe' notebooks/experiments/c_gfar_20261030/plot_results.py --out data/generated/evaluation/c_gfar_20261030_rerun01
```

The separate plotting environment contains Matplotlib only for artifact generation; it does not change the project lockfile or application environment. If absent, recreate with `uv --cache-dir .uv-cache venv .../.plot-venv --python .venv/Scripts/python.exe --system-site-packages`, then `uv --cache-dir .uv-cache pip install --python .../.plot-venv/Scripts/python.exe matplotlib`. Plot dependency versions are recorded during final validation.

Primary design: A/C × AVG/AWM/GFAR; 105 constructed groups; shared A/C top100 unions; K10; GFAR depth20 with SDD ties; percentile proxy calibration and threshold .5. Minmax/threshold/depth, scorer-specific union and cached B/D diagnostics are separated. Description masks reuse exact previous IDs and seeds. A/C fixed evaluators are scorer-derived simulations, not independent satisfaction labels. The human-labelled block assessment is restricted to original fully judged pairs and one annotator’s historical sheets.

`REPORT.md` and CSV/NPZ/JSONL files live under the generated output directory. Read the exact frozen numerical decision rule and the separate independent-evidence adoption gate in `config.json`. Every source/model/document is hashed and protected. Recommendation order is not a validated itinerary.
