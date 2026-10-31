# Full-catalogue metadata A/B/C/D rerun — 2026-10-30

Use the supplied compact metadata as model input and the full companion only for canonical/provenance validation. Old profiles, membership lists, seeds and metric definitions remain fixed. New files are read-only; no application/database/GFAR change is made.

Run from the repository root using the existing offline semantic environment and an unused output directory:

```powershell
& 'notebooks/experiments/enriched_poi_round1_20261028/.semantic-venv/Scripts/python.exe' notebooks/experiments/ai_metadata_20261030/evaluate_metadata.py --out data/generated/evaluation/ai_metadata_20261030_rerun_01
& 'notebooks/experiments/enriched_poi_round1_20261028/.semantic-venv/Scripts/python.exe' -m unittest discover -s notebooks/experiments/ai_metadata_20261030 -p test_metadata.py -v
```

The primary relevance comparison fits new B/C models on the original 19 IDs and uses the same 38 historical judgments. Those grades came from one human completing two sheets and were not collected against revised summaries. Broader candidates have no relevance labels. Taxonomy bridge results preserve the previous representation; an explicit enriched-catalogue variant uses new detailed categories. Menu-fact and missing-description ablations use fixed models.

Sparse lexical storage preserves the previous TF/IDF equations and passes a dense historical-corpus score/order/tie control. Known postfreeze analysis additions and CRLF-only Git worktree normalization are documented separately; original frozen bytes remain protected. No model weights are trained or downloaded.
