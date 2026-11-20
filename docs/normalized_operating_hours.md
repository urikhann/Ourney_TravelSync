# Normalized schedule import

Install dependencies with `pip install -e '.[ingestion,postgres]'`.

Normalized schedules preserve separate daily opening windows, effective dates,
source timestamps, and evidence. The immutable `poi_hours_import_batches` and
`poi_normalized_hours` tables scope records by dataset, POI, and batch. They leave
the legacy hours/exceptions and archived datasets intact.

Planning data, place details, and availability checks read these schedules.
Existing date exceptions take precedence. A schedule before its effective date
is unavailable to the reader; an empty daily window means explicitly closed.
Parsing and identity validation do not establish independent venue verification.

## Normalize and validate

```powershell
.\.venv\Scripts\python.exe -B scripts/normalize_poi_hours.py --effective-from YYYY-MM-DD --out output/normalized_hours
.\.venv\Scripts\python.exe -B scripts/import_normalized_hours.py output/normalized_hours/normalized_hours.json
```

The normalizer accepts supported explicit recurring schedules and holds ambiguous,
seasonal, overnight, admission-cutoff, and unsupported closure rules for review.
The import command validates the manifest and source hashes without writing.

## Import into the local database

```powershell
.\.venv\Scripts\python.exe -B scripts/import_normalized_hours.py output/normalized_hours/normalized_hours.json --apply-local
```

`--apply-local` targets the local Compose database at `127.0.0.1:5432` using the
repository's PostgreSQL settings. It requires the unique active dataset and the
expected 3,570 POIs, saves a backup, checks POI names/coordinates, imports
atomically, and verifies readback. Identical batches are idempotent; an existing
batch ID with different content is rejected.

Retain the output directory, manifest, and source evidence. Collection does not
automatically import or activate schedules. Rebuild API, mocks, and planning-data
images after changing the shared reader code.
