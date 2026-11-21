# Reproducible enriched deployment

E5 hybrid is the accepted recommender, with the existing GFAR integration.
Budget is excluded from product scope. Driving is the supported planning mode.
Known hours remain enforced; unknown hours remain permitted with warnings.
This setup changes deployment consistency, not recommendation quality or source verification.

## Configuration and artifacts

API and recommender resolve `TRAVELSYNC_DATASET_SNAPSHOT` (default: packaged
`data/deployment_snapshot.json`). Compose points both at `/data/deployment_snapshot.json`.
It declares `busan-enriched-v4-20261121`, the V4 CSV, operating-info CSV, and
SHA256 hashes. `PLACES_VERSION`, legacy `DATASET_VERSION`, and `PLACES_CSV` overrides
must agree with that specification. A different CSV alone is insufficient.
Remove old V3/demo overrides from `.env` when adopting this release.
`PLACES_FOLLOW_ENV` no longer imports or activates datasets at startup.

The tracked `enrichment_v4_manifest.json.gz` is a deterministic gzip copy of the
original local `output/enrichment_import_20261121/manifest.json`, without changing
its payload. It includes exact JSON metadata, 74 normalized schedules, 1,388 evidence
records, 3,496 review records, suppression IDs, original source hashes and retrieval
provenance. Its SHA256 is recorded in the deployment specification. The V4 CSV
and baseline operating CSV are also tracked. No essential installation input is
missing. Ignored daily collection CSVs and the original database dump are not
needed for installation; rerunning collection/promotion preparation still needs
those original sources and an unchanged parent database. They have not been
reconstructed. The manifest's `source_sha256` values describe the original
promotion inputs; installation verifies the packaged artifact hashes instead.

Installation derives core POIs through the existing CSV mapping, attaches the
manifest's exact JSON metadata, applies baseline hours statuses and conflict
suppression, and stores normalized schedules/evidence/reviews atomically. It also
freezes baseline weekly and observed-date hours in version-scoped tables.
Fresh databases get the existing three sample 2026 holidays; this is not a complete
holiday calendar. Existing global hours, exceptions, holidays, datasets and runs
are left intact on upgrade. Explicit global dated exceptions keep precedence.
Historical versions without frozen hours retain their existing legacy readers.

This is the **API application schema**, not the separate package PostgreSQL
`database/schema.sql`/`ourney-travelsync db-init` schema. Use the procedure below
for Compose. `scripts/promote_poi_enrichment.py` remains an explicit new-snapshot
promotion tool with parent/source checks; it is not a fresh-install or restart command.

## Fresh installation

Copy `.env.example` to `.env`, set secrets locally, and retain the V4 defaults.
From the repository root, with Docker available:

```powershell
docker compose build api recommender planning-data
docker compose up -d db
docker compose run --rm --no-deps api python -m app.deployment --activate
docker compose run --rm --no-deps recommender python main.py --prewarm --download
docker compose up -d
```

`--download` explicitly permits fetching only the pinned checkpoint. Omit it if
the model is already downloaded. Both operations use the normal Compose data/cache
mounts. Initialization creates the application schema and commits snapshot content
and activation in one transaction. Without `--activate` it installs a DRAFT.
A repeated command must identify the now-active version:

```powershell
docker compose run --rm --no-deps api python -m app.deployment --activate --expected-active busan-enriched-v4-20261121
```

For local processes, install the API dependencies and package extras, export an
explicit `DATABASE_URL` pointing to your intended application database, and run
`python -m app.deployment` from `api/`. No dotenv is loaded by that module itself.
The root `.env` is passed by Compose. Do not use an unspecified database for setup.

## Upgrade / original local V4 adoption

Back up the existing application database through your normal backup procedure.
Read its active version before stopping the API; `/meta/deployment` on this release
shows it. On older releases query `dataset_versions` read-only. Build the new images,
then stop API workers while changing the active dataset. Example for a V3 database:

```powershell
docker compose stop api
docker compose run --rm --no-deps api python -m app.deployment --activate --expected-active busan-enriched-v3
docker compose run --rm --no-deps recommender python main.py --prewarm --download
docker compose up -d
```

Use the actual head with `--expected-active`, including V4 for adoption of the
previous locally promoted snapshot. Existing V4 content must match core fields,
metadata, schedule contents/batch hash, evidence and reviews. Only matching
original V4 can acquire the frozen baseline hours. Missing or changed content
fails; it is never silently repaired or overwritten. A later snapshot requires
new immutable artifacts and a distinct version. Old runs keep their original
version; regenerate recommendations to use the newly activated catalogue.

## Restart and rollback

An ordinary `docker compose up -d` validates the configured active dataset and all
required snapshot records. It neither imports nor switches the active version.
If configuration points at an archived version it fails clearly. Repeated explicit
initialization validates before reuse and archives only the old active version
when activation was requested with the correct expected head.

Rollback is an explicit release operation: stop API writers, restore the retained
release's matching snapshot specification/configuration and images, prewarm its
catalogue, then run its snapshot initializer with `--activate --expected-active`
set to the current head. Keep all newer datasets and runs. This initializer can
activate only the snapshot declared by its hash-validated specification. The old
V3 release predates this setup format; rolling back to it requires a separately
verified V3 deployment bundle or the established database backup/release recovery
procedure. Merely setting `PLACES_VERSION=busan-enriched-v3` is deliberately rejected.
Never delete the volume or reset a shared database to test setup.

## E5 and deployment diagnostics

`python main.py --prewarm` in the recommender image resolves exactly the deployment
CSV, verifies hashes, uses model `intfloat/multilingual-e5-small` at revision
`614241f622f53c4eeff9890bdc4f31cfecc418b3`, and then checks scoring with regeneration
disabled. It exits unsuccessfully if actual hybrid scoring cannot be used. Output
includes model/text/content identity and a fixed public non-neutral probe's effective
method/fallback reason. Ordinary Compose requests remain cache-only
(`TRAVELSYNC_E5_REGENERATE=0`); cache failure retains feature fallback.

- API `GET /meta/deployment`: configured and active datasets, metadata/evidence/review
  counts, frozen legacy hours versions, normalized batch IDs/counts/hashes.
- Recommender `GET /health`: configured dataset, catalogue size, E5 model revision,
  cache identity and the fixed probe's effective scorer/fallback. HTTP success
  alone is not proof of E5: require `e5_hybrid` and null fallback.
- Planning-data `GET /diagnostics`: same database version/batch diagnostics; uses
  the existing bearer token when configured. No secrets/connection URLs are returned.
- Recommendation response `scoring.members`: effective method and fallback reason
  per actual member. Its cache identity must match recommender health and prewarm.
  A neutral member can intentionally fall back even with a healthy E5 cache.

Recommendation runs already transmit their full pinned database catalogue. Planning
candidates and planning-data requests use the run's version, including archived
versions; normalized and frozen baseline hours use that exact version. Recommender
CSV defaults never substitute for a supplied pinned snapshot. Cache identity covers
the complete scoring inputs and text, not only the dataset name. The regression
suite checks database/CSV identity equality and all 74 schedules across seven days.

## Validation on 21 November 2026

Executed checks: 100 focused core/service tests and 88 API tests passed (one API
live optimizer test skipped). All 13 dedicated deployment tests passed, including actual
E5 through an opt-in local cache check. Localhost HTTP verification started the real
API, recommender and planning-data processes against a disposable SQLite database:
explicit fresh setup succeeded, an API recommendation persisted actual E5 hybrid
scoring with matching cache identity, and API restart preserved both diagnostics
and that historical run. The in-process planning reader matched all 518 schedule/date
combinations. These are local checks with synthetic preferences, not external-provider
verification. Logs and smoke output are under `output/deployment-*-tests*.txt` and
`output/deployment-live-verification.json`. Routine regression tests use
isolated SQLite and synthetic provider/embedding fixtures. The opt-in command below
uses the actual local E5 cache without replacing embeddings; it exercises the
production recommender with the freshly initialized database catalogue and verifies
non-null semantic scores, effective hybrid method and exact cache identity.

```powershell
$env:TRAVELSYNC_REAL_E5_INTEGRATION='1'
$env:TRAVELSYNC_E5_CACHE='output/e5-cache'
.\.venv\Scripts\python.exe -m pytest tests/test_deployment_consistency.py -q -p no:cacheprovider
Remove-Item Env:TRAVELSYNC_REAL_E5_INTEGRATION
```

No Docker/container or disposable PostgreSQL verification was available in this
session. No external routing provider, new model download, venue-hour accuracy or
recommendation-quality evaluation is established by these tests. Shared application
data was not changed; no push, merge or remote deployment was performed.


Snapshot artifact hashes are over the exact packaged bytes. `.gitattributes`
marks the V4 and operating CSVs as `-text`, preserving their original CRLF bytes
on Linux and Windows. Do not regenerate the manifest hashes to accommodate a
platform's newline conversion. CI checks Git-stored and checked-out artifacts
before installing dependencies, and the installed-wheel check validates the same
snapshot. Regression tests round-trip the data through Git with both
`core.autocrlf=false` and `true`.
