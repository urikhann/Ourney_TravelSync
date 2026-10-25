# Collection, fixed-input processing and publication

Install `uv sync --extra ingestion`. Export `TOUR_API_KEY` and
`KAKAO_REST_API_KEY` in the environment. The optional collector also supports
Colab Secrets. The PostgreSQL connection alone loads a root `.env` file;
the collector does not implicitly load `.env`.

```powershell
uv run --extra ingestion ourney-travelsync collect --output data/generated
uv run --extra ingestion ourney-travelsync reprocess data/raw/tour.json data/raw/kakao.json --output data/generated/reprocessed
```

Raw JSON files must contain lists of the original source API records. The collector
retains `tour_original.json`, `kakao_original.json`, Kakao cache, processed source
CSVs, duplicate audit, feature validation template and run report. TourAPI raw
collection is not a permanent replay cache; retain both raw JSON files for replay.
All generated output belongs under ignored root `data/` (for example `data/raw/`,
`data/generated/`, `data/cache/`). No raw responses belong under `src/`.

The final generated name is `travelsync_generated_places.csv`, distinct from the
official packaged v2 snapshot. Both output and cache paths are rejected if they
overlap installed package source. Partial collection writes a partial-run folder
and the CLI exits unsuccessfully; it cannot be imported by `collect-to-db`.

`prepare(tour_rows, kakao_rows)` separates normalization, source-ID deduplication,
rule classification, conservative cross-source merging and final eligibility
filtering from HTTP. `reprocess` invokes that function with fixed JSONs and records
input hashes. Given the same code/dependencies and input ordering, this processing
is reproducible. Live API results, pagination and provider state can change.
The supplied official snapshot has no accompanying raw inputs, so its exact
historical reconstruction is not claimed.

For human feature labels, pass `collect --validation-csv PATH`; the existing
evaluation reports coverage and mismatches, including unmatched/ambiguous names.
It does not invent labels or operational opening hours, costs or festival dates.

Database publication requires separate steps:

```powershell
uv run --extra postgres ourney-travelsync db-import data/generated/travelsync_generated_places.csv --validate-only
uv run --extra postgres ourney-travelsync db-import data/generated/travelsync_generated_places.csv
uv run --extra postgres ourney-travelsync db-check
uv run --extra postgres ourney-travelsync db-activate NEW_DATASET_UUID --expected-head CURRENT_DATASET_UUID
```

Omit `--expected-head` only for an empty head. `collect-to-db` combines complete
collection and non-activating import; it never switches the active dataset.
The Python import APIs retain explicit `activate=True` support for trusted callers,
including their expected-head guard. Reviewed USER submissions retain the handoff's
atomic review/publish behavior; an administrator's approval explicitly publishes
a new snapshot. No automatic startup import or activation occurs.

DVC scaffolding was empty and has been removed. `experiments` is an optional
dependency extra for future MLflow/DVC work, with no remote or pipeline claimed.
Existing local DVC caches, environments, `.env`, `dvclive` and `mlflow.db` are preserved.
