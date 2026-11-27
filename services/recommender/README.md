# Recommender service (port 8002)

Exposes the recommender package (`src/ourney_travelsync`: scoring, AWM, GFAR) behind the
HTTP contract in `contracts/recommend.*.json`, so the backend can call it like the mock.
It only translates — the algorithm lives in the package.

| | Mock (`services/mocks`, 8001) | This service (8002) |
|---|---|---|
| Algorithm | reference weighted-feature implementation | **package scoring and aggregation** |
| Data | versioned backend POI/metadata JOIN | backend `poi_snapshot` when supplied; otherwise packaged enriched CSV |
| Scores | 0–1 | E5: relative relevance [0,1]; feature mean: 1–5; legacy TF-IDF: cosine |
| Ties | place id | seeded hash, **one seed per trip** (repeatable for a trip, different between trips) |

## Run (needs Python 3.13, like the package)

```bash
uv sync --locked --extra semantic
uv pip install -r services/recommender/requirements.txt
uv run --no-sync ourney-travelsync e5-cache --download
uv run --no-sync uvicorn main:app --app-dir services/recommender --port 8002
```

Run these commands from the repository root. Check
http://localhost:8002/health → `"places": 3570`.
Point the backend at it: in `api/.env` put `RECOMMENDER_URL=http://localhost:8002` and start the backend with
`--env-file .env`.

## Options in the request

- `method`: `gfar` (default) · `awm` (veto below `awm_threshold`, default 2.0 on 1–5) · `average`
- `top_l`: each member's candidate list (default 100) · `group_k`: length of the group list (default 30)
- `tie_seed`: optional; default is derived from `trip_id`
- `individual_method`: `e5_hybrid` (default), `feature_mean`, legacy `binary` alias,
  or `tfidf` (explicit positive taste text). Optional `alpha=.5`, `beta=1`.
  AWM/average comparisons route hybrid requests to feature mean; AWM rejects
  TF-IDF and requires thresholds in [1,5] (default 2.0).
- `poi_snapshot`: backend's version-pinned POI/metadata JOIN. Every record must
  carry the request's `dataset_version`. This avoids substituting the newest CSV
  for an archived database version. See [metadata integration](../../docs/poi_metadata.md).
- member `exclusions.resolved`: `{poi_id: reason}` — places the backend matched to a member's rule (e.g. hiking)

Environment: `DATASET_VERSION` (default `busan-enriched-v3`), `PLACES_CSV`, `GFAR_DEPTH` (20), `AWM_THRESHOLD` (2.0).

See [scoring equations, caching, fallback, and API diagnostics](../../docs/e5_hybrid.md). Prewarm `TRAVELSYNC_E5_CACHE` using the
pinned checkpoint; requests use offline cache-only operation by default. Empty
outputs carry exhaustion diagnostics. Hybrid scores are not satisfaction or probabilities.

## Test

```bash
uv run --no-sync pytest services/recommender/tests -q
```
