# Service contracts

Request and response examples are stored as paired `*.request.json` and
`*.response.json` files. Services validate schema and dataset versions before
processing a request. Typed optimizer contracts define scheduling facts and routes.

## Shared rules

- The 10 features, always in this order: `food, cafe, nature, history, art, shopping, activity, entertainment, relax, festival`.
  `feature_schema_version` = `fs-1.0`. Requests and responses carry it plus `dataset_version`; a mismatch is an error.
- Member `ratings` remain **raw 1–5 integers**. Optional `tastes.keywords` from the onboarding survey supplies the query for Method C.
- Place features remain **binary 0/1**, several can be 1. `options.individual_method="tfidf"` uses enriched text
  and cosine scores on 0–1. The default `e5_hybrid` emits relative relevance
  (`score_scale=relative_relevance_0_1`); `feature_mean`/historical `binary` use 1–5 in the real service.
  Consumers must use the returned `score_scale`; thresholds must match that scale.
- Personal exclusions (`exclusions.tags`, `exclusions.poi_ids`) are E1 hard rules. A low rating (1) is **not** an exclusion.
  Supported tags: `hiking`, `raw_fish`, `alcohol` (derived from the place category at import).
- Optional `members[].tastes` = `{"keywords": [...], "avoid_keywords": [...]}` from the onboarding survey
  (`GET /meta/tastes`), only for members who answered it. Korean words for TF-IDF (Method C). Ignore it if unused.
- Unknown is `null`, never 0: cost `null` = unknown, not free. Times are local `HH:MM`, dates `YYYY-MM-DD`, money KRW.
- Group list: GFAR. Return `group_rank`, `mean_score`, `selection_gain`, `group_utility` separately.
  `method: "average" | "awm"` exist only for comparison .
- Keep request/response examples consistent with the typed service contracts.

| Service | Endpoint | Contract |
|---|---|---|
| Recommender | `POST /recommend` | `recommend.*.json` |
| Optimizer | `POST /plans`, `POST /runs` | `optimizer.synthetic.*.json`, [typed models](../src/ourney_travelsync/optimizer/contracts.py) |
| Planning data | `POST /snapshot` | [PlanningSnapshot](../docs/optimizer_app_integration.md) |
| Availability | `POST /availability/check` | `availability.*.json` |
| Agent | `POST /agent/turn` | `agent.*.json` |
| Mock planner | Mock `POST /plans` | `plans.*.json`; incompatible with optimizer requests |

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
`available: null` when the hours parser has no usable schedule for that
date. `false` means a known conflict/closure. App planning allows unknown hours with warnings and enforces known intervals
and closures. Direct optimizer clients default to rejecting unknown hours. POI identity and metadata use the pinned
version; the legacy `poi_hours` and `poi_exceptions` tables remain keyed by place
ID alone and do not provide historical operating-schedule snapshots.

The optimizer uses `optimizer-1.0`: explicit dated windows, endpoint IDs,
group budget, transport, directed evidenced routes, member constraints and pinned
operating facts. The example files are **synthetic testing examples only**.
Missing production facts must not be copied from them. The legacy request cannot
be upgraded by adding defaults. See [optimizer integration requirements](../docs/optimizer.md).

**Unsupported API:** asynchronous runs (`POST /groups/{id}/runs` →
202 + run_id, `GET /runs/{id}`). The app sends enriched Module H
snapshots via a configurable trusted planning-data adapter; see
[the app contract and configuration](../docs/optimizer_app_integration.md).
The optimizer `/runs` is a synchronous internal budget coordinator,
and does not implement an asynchronous group-run API.
