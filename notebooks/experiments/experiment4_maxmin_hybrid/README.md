# Experiment 4: Max-Min and Hybrid

This isolated offline experiment reuses Experiment 3's exact frozen bundle at `data/generated/evaluation/c_gfar_20261030_run01/`. Source POIs, production code, previous experiments, labels and SRS/SDD remain protected. It requires NumPy/SciPy in the existing semantic environment; plotting uses the existing separate Matplotlib environment. No new dependency is installed.

From repository root in PowerShell:

```powershell
$experimentPython = 'notebooks/experiments/enriched_poi_round1_20261028/.semantic-venv/Scripts/python.exe'
$plotPython = 'notebooks/experiments/c_gfar_20261030/.plot-venv/Scripts/python.exe'
& $experimentPython -m unittest discover -s notebooks/experiments/experiment4_maxmin_hybrid -p 'test_*.py' -v
& $experimentPython -m unittest discover -s notebooks/experiments/c_gfar_20261030 -p 'test_*.py' -v
& '.venv/Scripts/python.exe' -m pytest tests -q -p no:cacheprovider --basetemp notebooks/experiments/experiment4_maxmin_hybrid/.test-temp
& $experimentPython notebooks/experiments/experiment4_maxmin_hybrid/experiment4.py --out data/generated/evaluation/experiment4_maxmin_hybrid_fresh01
& $plotPython notebooks/experiments/experiment4_maxmin_hybrid/plot.py --out data/generated/evaluation/experiment4_maxmin_hybrid_fresh01
& $experimentPython notebooks/experiments/experiment4_maxmin_hybrid/experiment4.py --out data/generated/evaluation/experiment4_maxmin_hybrid_fresh02
& $experimentPython notebooks/experiments/experiment4_maxmin_hybrid/verify.py --out data/generated/evaluation/experiment4_maxmin_hybrid_fresh01 --repeat data/generated/evaluation/experiment4_maxmin_hybrid_fresh02
```

Use fresh output directory names: the runner refuses overwrites. The repeat must finish before changing the first run's protected files. It compares complete scientific CSV tables and decompressed traces, excluding noisy runtime measurements and manifests.

`optimizer.py` contains reusable greedy and exact tiny-instance implementations. `experiment4.py` freezes decisions before results, replays historical baselines, measures stages and executes primary/masking/sensitivity/historical human-label conditions. `verify.py` independently reconstructs rank relevance, recomputes every simulated utility and replays all primary new-method rankings using a scalar reference. `REPORT.md`, generated tables, NPZ input references, selection traces, figures and verification evidence live in the output folder. Empty and undefined values are blank/JSON null, never invented zeros.

Primary: C-rank assessor, rank-proxy optimization, shared original A/C top100 union, K10, 105 frozen groups. A-rank is secondary; neither is independent satisfaction evidence. All five lambda values remain reported. Lambda1 differs from historical AVG. Human labels come from two sheets by one person and are used only for restricted descriptive assessment. No production adoption is supported by proxy optimization alone.
