# Versioned POI metadata

The team sidecar `poi_metadata_for_team.csv` contains 3,570 unique
`(place_id, dataset_version)` keys for `a063d4af-9004-4f13-aebe-ec6f174a7010`.
Its SHA256 is `a3a9a266cd486fda66e692bfe31f50afc3e0729d4d7a881b113f4d9e73e32f46`.
The packaged catalogue has the same 3,570 place IDs. That offline comparison does
not establish that an unavailable team database contains the intended version;
the importer checks the target database before writing anything.

## PostgreSQL recommendation package (`poi`)

Apply migrations explicitly using the configured database connection (credentials
stay in `.env` or environment variables):

```powershell
uv run --locked --extra postgres ourney-travelsync db-migrate
uv run --locked --extra postgres ourney-travelsync db-import-metadata 'D:\Backup\Documents\KakaoTalk Downloads\poi_metadata_for_team.csv' --dataset-version a063d4af-9004-4f13-aebe-ec6f174a7010 --validate-only
uv run --locked --extra postgres ourney-travelsync db-import-metadata 'D:\Backup\Documents\KakaoTalk Downloads\poi_metadata_for_team.csv' --dataset-version a063d4af-9004-4f13-aebe-ec6f174a7010
```

The target version must already exist. The importer requires exactly 3,570 rows,
one version, unique keys, the exact target POI set, and agreement of supplied core
fields (coordinates allow the schema's seven-decimal rounding). It never changes
`poi` or activates a dataset. Validation and inserts share one transaction.
Identical imports are idempotent; conflicting payloads are rejected. If a dataset
has already been used in a run, missing metadata cannot be added: create a new
dataset snapshot instead. Existing metadata is immutable even before a run.

Migration `004_poi_metadata.sql` preserves legacy enrichment verbatim in the new
table. New full-catalogue imports and dataset clones also write separate metadata.
The legacy POI JSON column remains for schema compatibility; queries read the new
table and new imports leave the legacy column empty.

```sql
SELECT p.*, coalesce(m.metadata, '{}'::jsonb) AS enriched_metadata
FROM poi AS p
LEFT JOIN poi_metadata AS m
  ON p.place_id = m.place_id AND p.dataset_version = m.dataset_version
WHERE p.dataset_version = :dataset_version;
```

Metadata is JSONB to retain every supplied enrichment/provenance field without
turning raw text into verified constraints. For named fields use, for example,
`m.metadata->>'description_summary'` and `m.metadata->>'food_type'`.
`get_run_inputs()` always supplies `recommendation_runs.dataset_version`, rather
than the current dataset head. Missing sidecars yield `{}` and retain the POI.
Dataset locks serialize metadata import with run creation.

## Web API (`pois`)

The API has a separate SQLAlchemy schema; do not apply `database/schema.sql` to
it. Startup creates its `poi_metadata` table with the same composite identity and
copies existing legacy payloads unchanged. `Poi.metadata_record` uses a LEFT JOIN
on both identity fields for recommendation/itinerary enrichment and detail reads.

For an existing API dataset whose version matches the CSV, run from `api/`:

```powershell
python -m app.import_metadata 'D:\Backup\Documents\KakaoTalk Downloads\poi_metadata_for_team.csv' --version a063d4af-9004-4f13-aebe-ec6f174a7010 --validate-only
python -m app.import_metadata 'D:\Backup\Documents\KakaoTalk Downloads\poi_metadata_for_team.csv' --version a063d4af-9004-4f13-aebe-ec6f174a7010
```

An API dataset named `busan-enriched-v1` is **not** the CSV's UUID version. Do not
relabel it implicitly. Import a new full dataset with the intended version if
needed using `app.import_places`, which now also populates the separate sidecar.
Existing runs continue to read their original version. The backend sends the
versioned JOIN result in `poi_snapshot` and persists it in the run request; the
real service validates each row's version and does not replace it with its CSV.
Legacy direct service calls without a snapshot still require its configured CSV
version. The mock service reads both tables using the requested version.

## Method C

Method C (`individual_method="tfidf"`) joins the optional onboarding survey's
`tastes.keywords` with spaces and scores that explicit positive query against
exactly `detailed_category`, `food_type`, `representative_menu`, `activity_type`,
and `description_summary`, in that order. It uses scikit-learn character 2/3-gram
TF-IDF, smooth IDF, L2 normalization and cosine similarity on [0, 1]. The vectorizer
fits only eligible POIs, after exclusions. Empty/missing metadata, empty/OOV queries
and null sentinels contribute no text. `representative_activity` is retained and
displayed but is not silently substituted for the absent `activity_type` field.

New CLI and API requests use Method C. The Python pipeline and legacy service
requests retain the binary default for compatibility; existing DB configs lacking
`individual_method` replay with binary scoring. For explicit historical behavior
use `--individual-method binary` or the equivalent request option. New CLI run
configs store the method and score scale. The web/API uses upstream's per-user
multiple-choice survey (`UserTaste`, `/me/tastes`) and keeps its keywords in the
run request. The redundant per-trip free-text API field and textarea were removed.
The standalone PostgreSQL/CLI path can still accept text using
`--preference-texts FILE.json`, a mapping of user IDs to text recorded in the
config snapshot, or a versioned standalone DB preference. This separate package
schema does not add a free-text field to the web API's preferences table.

No query is invented from the ten ratings. Skipped/missing tastes produce zero
similarity; GFAR may still return deterministic tied candidates. This is not
evidence of relevance. GFAR, candidate-union mechanics, constraints and itinerary
algorithms are unchanged. AWM's default threshold is 0.25 for Method C and 2.5 for
the binary 1–5 scorer; callers should set a threshold appropriate to their method.
`avoid_keywords` remain in the captured request; they are not positive query terms
and no new soft-dislike penalty is introduced. Upstream's existing exclusion tags
still enforce the survey's hard rules. The API uses upstream's `PlaceAbout` and
`ReportedHours` components for metadata display rather than a duplicate UI.

## Operating data

Availability requests carry the displayed/run dataset version for selecting the
POI. The upstream `hours.py` parser, `import_hours.py`, weekly/dated hours handling,
and unknown-hours UI are retained. The superseded local `metadata_hours.py` adapter
was removed; no extra verification flag absent from the upstream pipeline is
required. Unknown availability stays `available: null`, and the upstream demo
optimizer keeps unknown schedules without treating them as confirmed opening.

The separate metadata table remains versioned, but upstream's parsed `poi_hours`
and `poi_exceptions` are keyed by place ID alone. Its importer updates them across
versions. Historical operating-schedule reproducibility is therefore still a
limitation; this cleanup does not claim to version those tables or change the
upstream parser's operating-data policy.

OR-Tools duration, cost and operating constraints remain future work. No metadata
value has been promoted to a solver constraint in this change.

## Reproducible verification

Use only a disposable database through `TRAVELSYNC_TEST_DATABASE_URL`:

```powershell
uv run --locked --extra postgres pytest -q integration_tests
uv run --locked --extra postgres python scripts/verify_metadata_import.py 'D:\Backup\Documents\KakaoTalk Downloads\poi_metadata_for_team.csv' --report data/generated/metadata_integration/import_report.json
```

The script creates a random isolated schema, loads core POIs with the CSV's exact
version, imports all 3,570 sidecars, checks the POI rows are unchanged, runs Method
C through GFAR, and replays the captured run after changing the active head. It
also verifies metadata mutation is blocked. By default it drops only its own
isolated schema; `--keep-schema` retains the evidence tables. It never touches
the shared/default schema's data. A successful isolated run does not establish
deployment to the team database or improvement in recommendation quality.
