# Cleanup and teammate integration report

Date: 2026-10-24 (Asia/Seoul). Starting checkout: `main`, `2134ab5`, matching local
`origin/main`. No applicable AGENTS.md was found in the repository or its ancestors.
The original local `.dvc/.gitignore.backup` was preserved. No push or publication.

## Resolution of review findings

| Review finding | Resolution |
|---|---|
| Separate dependency manifests | pyproject.toml owns core/extras/dev; actual uv resolver regenerated uv.lock; independent requirements.txt removed |
| Missing reproducible data preparation | Teammate collector under ingestion; fixed-JSON replay; raw/output/report contracts documented |
| Empty DVC configuration | Tracked scaffolding removed; future tooling optional; local cache preserved |
| Dead AGREE | Commented module and disabled imports/tests removed; design notes in agree_design.md |
| Scaffold CLI/description | Real description and CLI for recommendations, collection/replay and DB workflows |
| Notebook inside library | Moved to notebooks; tested evaluation.py extracted; stale outputs cleared |
| Mixed naming | recommenders/ and lowercase modules; imports, tests, scripts and docs updated |
| Misleading README tree/status | Actual Python scope and commands documented; planned services separate |
| No automated verification | Offline tests, opt-in DB tests, CI, hand-calculated checks and wheel smoke test |
| Candidate tie bias | Seeded identity policy, strict score order retained, reordered-input tests and v2 measurements |
| Local clutter/data concerns | One official v2 CSV; obsolete snapshot removed; ignored local records retained |

## Additional handoff corrections

Broken pipeline imports now use installed-package paths. Feature order is defined
once in features.py; data_schema.py shares CSV columns. HTTP, PostgreSQL, pandas
and Streamlit imports are isolated from ordinary recommendations. Requests is
included in ingestion dependencies. Scripts and the app are thin entry points
without sys.path insertion.

The PostgreSQL schema, immutable versions, preferences, run snapshots, result
separation and reviewed submissions were retained. The new adapter converts
captured POIs/preferences with deterministic UUID alignment, applies exclusions,
and saves individual/group results atomically.

Migration 002 resolves the handoff's incompatible 0-1 SQL checks versus working
1-5 recommendation scores. Undefined AWM gain, optimizer utility and feasible
dates can be NULL. Historical results block automatic migration and require review.

## Official data validation

- Location: src/ourney_travelsync/data/travelsync_final_places_v2.csv.
- SHA256: 5fe6fc689b10c03b0a4b1dd6ff99db4cf33e0d2478b20aaf32a6c51929127a15.
- Direct archive-byte copy; no regeneration or duplicate official CSV.
- 3,570 records, 29 columns, 3,570 unique IDs; ten binary features in shared order,
  at least one label per POI and valid coordinates.
- Sources: 345 TOUR_API, 106 TOUR_API+KAKAO, 3,119 KAKAO; 21 feature patterns.
- Strict DB CSV validation passes offline; generic loaders tested on other sizes.

See tie_policy.md and tie_diagnostics.json for measured tie frequencies and source
composition. These diagnostics do not establish improved relevance or fairness.

## Verification

An isolated editable `.venv-review` was installed through normal uv sync with
ingestion, postgres and viewer extras. Existing environments were not changed.

- Offline tests with optional components: 42 passed, with 21 existing unittest subtests.
- Core-only installed-wheel suite: 37 passed, 5 optional-component skips, 21 subtests.
- Notebook: all code cells executed with installed imports and v2 data.
- CLI: AWM/GFAR, help and invalid-input checks passed.
- Streamlit: empty/populated AppTest flows passed with mocked DB access.
- Bytecode compilation and git diff whitespace checks passed.
- uv wheel/source builds succeeded; modules and packaged CSV inspected.
- Wheel installed with locked NumPy, loaded data and ran CLI outside the checkout;
  optional HTTP/DB/viewer modules were not imported.
- PostgreSQL integration not run locally: no disposable test URL configured and
  Docker has no running engine. Separate integration suite skips for that reason.
  No existing database was connected to, initialized, migrated, imported or activated.
- CI is added but not executed by this task. Its PostgreSQL service is disposable;
  offline checks do not prove SQL runtime or trigger correctness.

## Remaining limits and migration steps

Follow the README uv workflow. Verify schema and integration tests on a disposable
PostgreSQL database before shared migration. Migration 002 refuses old results;
changed mapping-code hashes also require review when retaining historical USER
rows. Review a READY dataset, then explicitly activate with the expected head.

Original raw JSONs and historical run reports were absent from the handoff, so
exact snapshot-generation history is unverified. Live API calls can change and
were not executed. Routing, schedule/budget/accessibility feasibility, trained
AGREE, authentication endpoints and optimizer utility remain unimplemented.
Undefined database result fields remain NULL.
