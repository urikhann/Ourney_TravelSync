# Contracts — the agreement between the parts

Each teammate's service must accept the `*.request.json` shape and return the `*.response.json` shape.
The backend (`api/`) sends exactly these; the mock service (`services/mocks/`) returns exactly these.
Updated to **TS SRS / SDD 1.1** (2026-10-07).

Rules for everyone
- The 10 features, always in this order (SDD 8.1): `food, cafe, nature, history, art, shopping, activity, entertainment, relax, festival`.
  `feature_schema_version` = `fs-1.0`. Requests and responses carry it plus `dataset_version`; a mismatch is an error.
- Member `ratings` remain **raw 1–5 integers**. Optional `tastes.keywords` from the onboarding survey supplies the query for Method C.
- Place features remain **binary 0/1**, several can be 1. `options.individual_method="tfidf"` uses enriched text
  and cosine scores on 0–1. The default `e5_hybrid` emits relative relevance
  (`score_scale=relative_relevance_0_1`); `feature_mean`/historical `binary` use 1–5 in the real service.
  Consumers must use the returned `score_scale`; thresholds must match that scale.
- Personal exclusions (`exclusions.tags`, `exclusions.poi_ids`) are E1 hard rules. A low rating (1) is **not** an exclusion.
  Tags today: `hiking`, `raw_fish`, `alcohol` (derived from the place category at import).
- Optional `members[].tastes` = `{"keywords": [...], "avoid_keywords": [...]}` from the onboarding survey
  (`GET /meta/tastes`), only for members who answered it. Korean words for TF-IDF (Method C). Ignore it if unused.
- Unknown is `null`, never 0: cost `null` = unknown, not free. Times are local `HH:MM`, dates `YYYY-MM-DD`, money KRW.
- Group list: GFAR (SDD 9.2). Return `group_rank`, `mean_score`, `selection_gain`, `group_utility` separately.
  `method: "average" | "awm"` exist only for comparison (SRS: Should).
- Change a contract only with a pull request that updates these files. Everyone reviews it.

| Service | Owner | Endpoint | Files |
|---|---|---|---|
| Recommender (D, E1, F, G) | Recommender owner | `POST /recommend` | `recommend.*.json` |
| Itinerary optimizer (H, E2/E3) | Recommender owner (proposed) | New service `POST /plans`, optional shared-budget `POST /runs` | `optimizer.synthetic.*.json`, [typed contract](../src/ourney_travelsync/optimizer/contracts.py) |
| Legacy mock itinerary | Existing demo only | Mock `POST /plans` | `plans.*.json` (incompatible with the new optimizer) |
| Opening-hours enrichment + E2 | Data owner | *to be defined* (replaces `availability.*.json`) | — |
| AI agent | Agent owner | `POST /agent/turn` | `agent.*.json` |
| Place data | Data owner | CSV → `python -m app.import_places` | `src/ourney_travelsync/data/README.md` |

Reference implementation of the scoring + GFAR from the SDD: `services/mocks/group_rec.py`
(tests: `api/tests/test_group_rec.py` — score 10/30 and the GFAR fixture A, C, B).

The backend supplies `poi_snapshot` for the real recommender: an array of
`{place_id, place_name, category_name, dataset_version, latitude, longitude, enriched_metadata, food, cafe,
nature, history, art, shopping, activity, entertainment, relax, festival}`.
It comes from the database's version-aware LEFT JOIN. Each row must have the
request's exact `dataset_version`. The request (including taste keywords, options and POI
snapshot) is stored with the run. Direct legacy calls without this field use the
service's configured CSV and require its dataset version. Existing
`recommend.request.json` remains a legacy binary example; API requests default to
E5 hybrid. Additive scoring/fallback/status diagnostics are described in
[E5 integration](../docs/e5_hybrid.md). Group/list/utility fields are preserved;
hybrid satisfaction display fields are null. AWM comparisons require feature scores.

Availability checks carry `dataset_version` on each check (or at request level).
Omitting it uses ACTIVE only for standalone current-place checks. Responses use
`available: null` when the upstream hours parser has no usable schedule for that
date. `false` means a known conflict/closure. The upstream optimizer keeps unknown
hours without labelling them verified. POI identity and metadata use the pinned
version; the upstream `poi_hours` and `poi_exceptions` tables remain keyed by place
ID alone and do not provide historical operating-schedule snapshots.

The new optimizer uses `optimizer-1.0`: explicit dated windows, endpoint IDs,
group budget, transport, directed evidenced routes, member constraints and pinned
operating facts. The example files are **synthetic testing examples only**.
Missing production facts must not be copied from them. The legacy request cannot
be upgraded by adding defaults. See [optimizer integration requirements](../docs/optimizer.md).

**Still outside the live API contract:** async runs (`POST /groups/{id}/runs` →
202 + run_id, `GET /runs/{id}`). The production app now sends enriched Module H
snapshots via a configurable trusted planning-data adapter; see
[the app contract and configuration](../docs/optimizer_app_integration.md).
The new optimizer `/runs` is a synchronous internal budget coordinator,
not the planned asynchronous group-run API.
