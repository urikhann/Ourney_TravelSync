# Contracts — the agreement between the parts

Each teammate's service must accept the `*.request.json` shape and return the `*.response.json` shape.
The backend (`api/`) sends exactly these; the mock service (`services/mocks/`) returns exactly these.
Updated to **TS SRS / SDD 1.1** (2026-10-07).

Rules for everyone
- The 10 features, always in this order (SDD 8.1): `food, cafe, nature, history, art, shopping, activity, entertainment, relax, festival`.
  `feature_schema_version` = `fs-1.0`. Requests and responses carry it plus `dataset_version`; a mismatch is an error.
- Member `ratings` are the **raw 1–5 integers**. The recommender derives `w = r / sum(r)` and scores `P(u,i) = sum w·x` (0–1).
- Place features are **binary 0/1**, several can be 1. Scores are 0–1; never mix with the old 1–5 thresholds.
- Personal exclusions (`exclusions.tags`, `exclusions.poi_ids`) are E1 hard rules. A low rating (1) is **not** an exclusion.
  Tags today: `hiking`, `raw_fish`, `alcohol` (derived from the place category at import).
- Unknown is `null`, never 0: cost `null` = unknown, not free. Times are local `HH:MM`, dates `YYYY-MM-DD`, money KRW.
- Group list: GFAR (SDD 9.2). Return `group_rank`, `mean_score`, `selection_gain`, `group_utility` separately.
  `method: "average" | "awm"` exist only for comparison (SRS: Should).
- Change a contract only with a pull request that updates these files. Everyone reviews it.

| Service | Owner | Endpoint | Files |
|---|---|---|---|
| Recommender (D, E1, F, G) | Recommender owner | `POST /recommend` | `recommend.*.json` |
| Itinerary optimizer (H, E3) | Recommender owner (proposed) | `POST /plans` | `plans.*.json` |
| Opening-hours enrichment + E2 | Data owner | *to be defined* (replaces `availability.*.json`) | — |
| AI agent | Agent owner | `POST /agent/turn` | `agent.*.json` |
| Place data | Data owner | CSV → `python -m app.import_places` | `src/ourney_travelsync/data/README.md` |

Reference implementation of the scoring + GFAR from the SDD: `services/mocks/group_rec.py`
(tests: `api/tests/test_group_rec.py` — score 10/30 and the GFAR fixture A, C, B).

**Not yet in the contract (next):** async runs (`POST /groups/{id}/runs` → 202 + run_id, `GET /runs/{id}`),
group constraints (daily time windows, start/end points, total budget, transport), the single OR-Tools itinerary with
arrival/start/departure/travel/stay/cost per stop and `solver_status`. See the progress note.
