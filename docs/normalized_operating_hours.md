# Normalized schedule import

Install the script dependencies first with `pip install -e '.[ingestion,postgres]'`.

The 19 November 2026 batch imported 72 schedules into the local active `busan-enriched-v3` database: 46 previously UNKNOWN, 16 expired SNAPSHOT and 10 already recurring. Another 573 retrieved-hours records are held. See `output/normalized_hours_20261119/REPORT.md` for counts, reasons, backup and actual planning-container readback.

The new immutable tables `poi_hours_import_batches` and `poi_normalized_hours` avoid changing the global legacy hours/exceptions or archived datasets. Planner, place details and availability readers consult the scoped schedules and preserve separate opening windows. Effective dates and original source timestamps are retained. This path validates source identity and supported schedule structure; it does not assert independent venue verification. Existing date exceptions take precedence.

```powershell
.\.venv\Scripts\python.exe -B scripts/normalize_poi_hours.py --effective-from YYYY-MM-DD --out output/normalized_hours_YYYYMMDD
.\.venv\Scripts\python.exe -B scripts/import_normalized_hours.py output/normalized_hours_YYYYMMDD/normalized_hours.json
.\.venv\Scripts\python.exe -B scripts/import_normalized_hours.py output/normalized_hours_YYYYMMDD/normalized_hours.json --apply-local
```

The first import command validates without writing. `--apply-local` deliberately targets the existing local Compose database at 127.0.0.1:5432 using the repository's POSTGRES settings, requires the unique active dataset and expected 3,570 POIs, captures a backup, imports atomically and checks readback. Repeating an identical batch is idempotent; changing an existing batch ID is rejected. Source hashes and target name/coordinates are checked before import. Keep the complete dated output directory and its source evidence. No production activation or hours-enforcement change is performed.

For the current batch, only database and planning-data were started. Rebuild/start API/mocks later to use the new readers there. Daily collection remains collection/validation only; future imports are explicit.
