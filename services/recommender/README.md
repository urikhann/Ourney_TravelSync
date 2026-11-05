# Recommender service (port 8002)

Puts the team's recommender package (`src/ourney_travelsync`: scoring, AWM, GFAR) behind the
HTTP contract in `contracts/recommend.*.json`, so the backend can call it like the mock.
It only translates — the algorithm lives in the package.

| | Mock (`services/mocks`, 8001) | This service (8002) |
|---|---|---|
| Algorithm | stand-in written from the SDD | **the team package** (Urikhan) |
| Data | versioned backend POI/metadata JOIN | backend `poi_snapshot` when supplied; otherwise packaged enriched CSV |
| Scores | 0–1 | Method C: 0–1; historical binary: 1–5 |
| Ties | place id | seeded hash, **one seed per trip** (repeatable for a trip, different between trips) |

## Run (needs Python 3.13, like the package)

```bash
brew install uv                       # once
cd ~/dev/Ourney_TravelSync/services/recommender
uv venv --python 3.13 .venv           # once
source .venv/bin/activate
uv pip install -e ../.. -r requirements.txt   # once (and after the package changes)
python -m uvicorn main:app --reload --port 8002
```

Check http://localhost:8002/health → `"places": 3570`.
Point the backend at it: in `api/.env` put `RECOMMENDER_URL=http://localhost:8002` and start the backend with
`--env-file .env`.

## Options in the request

- `method`: `gfar` (default) · `awm` (veto below `awm_threshold`, default 2.5 on 1–5) · `average`
- `top_l`: each member's candidate list (default 100) · `group_k`: length of the group list (default 30)
- `tie_seed`: optional; default is derived from `trip_id`
- `individual_method`: `tfidf` (Method C, optional member `tastes.keywords`) or `binary`.
  Legacy direct calls default to binary; the API supplies `tfidf`. AWM defaults
  to 0.25 for TF-IDF, 2.5 for binary.
- `poi_snapshot`: backend's version-pinned POI/metadata JOIN. Every record must
  carry the request's `dataset_version`. This avoids substituting the newest CSV
  for an archived database version. See [metadata integration](../../docs/poi_metadata.md).
- member `exclusions.resolved`: `{poi_id: reason}` — places the backend matched to a member's rule (e.g. hiking)

Environment: `DATASET_VERSION` (default `busan-enriched-v3`), `PLACES_CSV`, `GFAR_DEPTH` (20), `AWM_THRESHOLD` (2.5).

## Test

```bash
python -m pytest -q
```
