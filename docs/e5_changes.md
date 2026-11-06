# E5 integration change inventory

Branch: `feat/e5-hybrid-recommender`. Implementation is saved as an integration
checkpoint commit; no push or merge into main was performed. Pre-existing untracked
`experiments/` contents were preserved and are excluded from this inventory.

Phase A implemented the exact recovered November 5 weighted semantic math,
full-catalogue tied-percentile fusion, lazy CPU encoder, versioned shared cache,
and labeled member-specific fallback. Phase B connected the real API/service,
CLI, PostgreSQL adapter, unchanged GFAR, hard exclusions and score display.
Phase C executed core/service/API, real-E5/full-dataset, isolated PostgreSQL,
TypeScript/Vite and frozen-reference checks. Phase D documents behavior,
operations, evidence and deviations from old SRS/SDD text.

Important compatibility details: feature_mean/binary remain independently usable;
new requests default to e5_hybrid; AWM routes through feature scoring or explicitly
rejects incompatible direct configurations. Response additions are diagnostic.
Hybrid fit/satisfaction fields are null. The only stand-in optimizer change handles
that null group-fit value; its selection logic and agent handlers were not changed.
No canonical POI files, private environment files or unrelated agent components
were modified. A new additive migration is required only for the separate
PostgreSQL recommendation-package schema.

## Created

- `api/tests/test_e5_application.py`
- `database/migrations/005_e5_score_semantics.sql`
- `docs/e5_api_validation.json`
- `docs/e5_changes.md`
- `docs/e5_hybrid.md`
- `docs/e5_validation.json`
- `scripts/verify_e5_integration.py`
- `services/recommender/tests/conftest.py`
- `services/recommender/tests/test_e5_service.py`
- `src/ourney_travelsync/recommenders/hybrid.py`
- `tests/fixtures/e5_reference.json`
- `tests/test_e5_hybrid.py`

## Modified

- `.env.example`
- `.github/workflows/tests.yml`
- `.gitignore`
- `README.md`
- `api/app/routes/recommendations.py`
- `api/app/scores.py`
- `api/tests/conftest.py`
- `api/tests/test_flow.py`
- `api/tests/test_scores.py`
- `contracts/README.md`
- `database/migrations/README.md`
- `docker-compose.yml`
- `docs/poi_metadata.md`
- `integration_tests/test_postgres.py`
- `pyproject.toml`
- `services/mocks/main.py`
- `services/recommender/Dockerfile`
- `services/recommender/README.md`
- `services/recommender/main.py`
- `services/recommender/tests/test_service.py`
- `src/ourney_travelsync/cli.py`
- `src/ourney_travelsync/common.py`
- `src/ourney_travelsync/db/adapter.py`
- `src/ourney_travelsync/db/repository.py`
- `src/ourney_travelsync/recommenders/average_without_misery.py`
- `src/ourney_travelsync/recommenders/pipeline.py`
- `tests/test_cli.py`
- `tests/test_recommender.py`
- `uv.lock`
- `web/src/components/Scores.tsx`
- `web/src/types.ts`

## Verification evidence

- 87 core/service tests passed; 21 subtests passed.
- 48 API tests passed, including isolated SQLite full-catalogue JOIN/persistence.
- Full API warm measurements (SQLite, actual E5 cache and run persistence):
  [application report](e5_api_validation.json).
- Actual E5 embeddings passed the full-catalogue API test with controlled profiles,
  confirmed exclusions, JSON persistence and deliberately unavailable semantics.
- 2 PostgreSQL integration tests passed on a disposable local cluster, covering
  migration, preserved historical results and labeled hybrid fallback. The server
  was stopped after testing; the shared application database was not migrated.
- TypeScript compilation and Vite production build passed. npm was absent from
  the shell PATH; these same build entry points ran through the available Node runtime.
- uv.lock offline validation and git diff whitespace checks passed.
- Historical November 5 scoring equivalence and current warm HTTP performance:
  [machine-readable report](e5_validation.json).
- November 7 participant predictions/workbooks were unavailable. No exact human
  pilot reproduction or superiority claim is made. Fresh E5 encoding can change
  lower-rank near ties, as quantified in the report. Docker was not built.

The test run initially exposed outdated default-scale assertions, the legacy
mock recommender's lack of E5 support, fixture omissions and float32 re-encoding
differences. The baseline tests now select feature scoring explicitly; API tests
use the production recommender; regression comparisons hold frozen embeddings
fixed and separately report fresh-encoding drift. No failing test remains.

See [architecture, formulas, commands and limitations](e5_hybrid.md).
