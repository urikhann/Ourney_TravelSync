# Official POI snapshot

The current default is `travelsync_poi_metadata_enriched_v3.csv`, supplied on
2026-11-05: 3,570 POIs, 72 columns, SHA256
`3e35a4722b71d788d63c9ac1e4b5060c1df931a54cec739d7475389f404ec5e9`. It preserves every row and all 29 core
columns of the v2 snapshot below, adding 43 metadata and provenance columns.
The loader retains all columns; scoring still uses the same ten binary features.
Database imports retain the additions in `enriched_metadata`. Operating text and
verification fields remain source metadata; they do not establish availability.

V3 provides 2,252 non-missing `description_summary` values (63.1%); the literal
`NULL` is missing. Against the packaged v1, it fills 417 previously missing
descriptions and preserves all 1,835 existing descriptions. The supplied handoff
reports 91 additions over a 2,161-description version; that intermediate file was
not supplied for comparison and was not the packaged baseline. Only eight columns change: `description_raw`,
`description_summary`, `description_source_url`, `description_source_type`,
`description_basis`, `description_raw_format`, `description_checked_at`, and
`field_provenance_json`. All 29 core columns, remaining metadata, IDs and row order
are unchanged. The supplied v3 file is copied byte-for-byte.

The previous `travelsync_poi_metadata_enriched_v1.csv` remains available for
snapshot comparison (SHA256
`583046906e737845fe5673487a40f79a84401c146f23df516c8c39af94e33eb4`).
Existing databases require an explicit import as a new version; this repository
update does not import or activate data in a running database.

## Previous core snapshot


`travelsync_final_places_v2.csv` is the previous teammate handoff snapshot
from `travel.zip`, received 2026-10-24. Its bytes were copied directly from the
archive, not regenerated during repository cleanup.

- SHA256: `5fe6fc689b10c03b0a4b1dd6ff99db4cf33e0d2478b20aaf32a6c51929127a15`
- 3,570 records, 29 columns, 3,570 unique external `place_id` values.
- Sources: 345 TOUR_API, 106 TOUR_API+KAKAO, 3,119 KAKAO.
- Ten independent binary labels, at least one per POI, in this exact order:
  `food, cafe, nature, history, art, shopping, activity, entertainment, relax, festival`.
- v2 contains 21 distinct feature vectors. Labels are rule-based assumptions,
  not ground-truth user relevance judgments.

The installed loader uses `importlib.resources`; `load_places(path)` accepts an
explicit external CSV. Generic loaders do not require 3,570 rows. Fixed identity
checks live separately in `tests/test_snapshot.py`.

The CSV `place_id` is an external identity (`tour:…` / `kakao:…`). PostgreSQL uses
`poi_id = uuid5(NAMESPACE_URL, 'travelsync:' + place_id)` and the composite key
`(poi_id, dataset_version)`. Database recommendations preserve the database UUIDs.
`dataset_version` is a separately assigned immutable import snapshot, not the
filename's v2 suffix. The import's `input_sha256` hashes normalized validated rows;
it is distinct from the byte checksum above.

The supplied pipeline is integrated under `ingestion/`. Raw inputs and the original
collection run report were not supplied, so the snapshot's historical API calls
and exact generating code cannot be independently reconstructed from this CSV.
Live collection can change; fixed-JSON reprocessing is a separate workflow.
Generated files go in ignored root `data/`, never here. Importing generated data
does not replace this file or activate a database dataset.

The old 3,591-row snapshot is available in Git history only. See
`docs/data_workflows.md` and `database/CSV_MAPPING.md` for processing and import.
