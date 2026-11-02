# Current TF-IDF evaluation

This experiment reads the current enriched catalogue, the frozen Korean
scenarios, and verified historical human annotations. It changes no production
files. `protocol.json` fixes settings before execution; no parameter is selected
from scores or labels. The report distinguishes retrospective small-pool label
transfer from unavailable full-catalogue recommendation quality.

From the repository root:

```powershell
.venv/Scripts/python.exe -m pytest notebooks/experiments/tfidf_evaluation_20261031/test_evaluate_tfidf.py tests/test_tfidf.py -q -p no:cacheprovider --basetemp data/generated/tfidf-evaluation-tests
.venv/Scripts/python.exe notebooks/experiments/tfidf_evaluation_20261031/evaluate_tfidf.py
# A second destination allows byte-for-byte comparison; outputs are never overwritten.
.venv/Scripts/python.exe notebooks/experiments/tfidf_evaluation_20261031/evaluate_tfidf.py --destination data/generated/evaluation/tfidf_vs_binary_20261031_repeat01
.venv/Scripts/python.exe notebooks/experiments/tfidf_evaluation_20261031/validate_tfidf_results.py data/generated/evaluation/tfidf_vs_binary_20261031_verified data/generated/evaluation/tfidf_vs_binary_20261031_repeat01 --output data/generated/evaluation/tfidf_vs_binary_20261031_validation.json
```

The original annotation-audit and NDCG functions are loaded unchanged from the
existing `exp` branch via AST extraction. The original independent-rater CLI
guard remains intact; this separate script truthfully reports one human's two
rating sheets. It verifies original human source files and frozen packet
evidence. No prior metric/ranking file is an input.

Precision uses requested K as denominator even for short judged lists; all
scopes and null values are explicit. A single known relevant item is not enough
to measure catalogue Recall or NDCG. The strict unchanged-evidence comparison
is withheld when fewer than two judged candidates remain per scenario.

The delivered run is `data/generated/evaluation/tfidf_vs_binary_20261031_verified/`.
It includes CSV/JSON results and REPORT.md. Independent validation is written
beside that directory and enumerates every within-tie permutation to check all
metric rows, checks the original frozen scenario/annotation hashes, verifies
all current protected sources, and compares the fresh repeat byte for byte.

Validation completed on 2026-10-31: 57 tests and 21 subtests passed; one opt-in
PostgreSQL integration test was skipped because no disposable database was
configured. The independent validator checked 864 metric rows and 128,520 rank
rows; maximum metric discrepancy was 8.22e-15. All 18 final result files were
byte-identical in the clean repeat. `git diff --check` passed. No production
files, dataset, GFAR code, constraint logic or source labels changed during this
evaluation (the earlier implementation's uncommitted changes remain intact).
