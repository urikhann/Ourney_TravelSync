# Official POI snapshot

The current default is `travelsync_poi_metadata_enriched_v3.csv`, containing 3,570 POIs, 72 columns, SHA256
`3e35a4722b71d788d63c9ac1e4b5060c1df931a54cec739d7475389f404ec5e9`. It preserves every row and all 29 core
columns of the v2 snapshot below, adding 43 metadata and provenance columns.
The loader retains all columns; scoring still uses the same ten binary features.
Database imports retain the additions in `enriched_metadata`. Operating text and
verification fields remain source metadata; they do not establish availability.

V3 provides 2,252 non-missing `description_summary` values (63.1%); the literal
`NULL` is missing. Description text, source references, and provenance are retained as supplied.
The source fields do not establish current venue hours or independently verified
recommendation relevance.

The previous `travelsync_poi_metadata_enriched_v1.csv` remains available for
snapshot comparison (SHA256
`583046906e737845fe5673487a40f79a84401c146f23df516c8c39af94e33eb4`).
Existing databases require an explicit import as a new version; startup does not replace existing dataset snapshots.

## Previous core snapshot


`travelsync_final_places_v2.csv` contains the core fields used by the enriched
catalogue. Its checksum supports byte-level snapshot verification.

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

The collection pipeline is under `ingestion/`. A CSV alone does not reconstruct
the API responses or exact execution that generated it; replay requires raw inputs
and a run manifest.
Live collection can change; fixed-JSON reprocessing is a separate workflow.
Generated files go in ignored root `data/`, never here. Importing generated data
does not replace this file or activate a database dataset.

See [data workflows](../../../docs/data_workflows.md) and
[CSV mapping](../../../database/CSV_MAPPING.md) for processing and import.
