# Packaged dataset snapshots

The V3, V4 and V5 catalogues each contain 3,570 Busan POIs. Selection is explicit; cleaning
the repository does not import, activate or amend application data.

| Consumer / release | CSV | Selection |
|---|---|---|
| Python loader and CLI default | `travelsync_poi_metadata_enriched_v3.csv` | `load_places()` without a path |
| Default application deployment, V4 | `travelsync_poi_metadata_enriched_v4.csv` | `deployment_snapshot.json`, version `busan-enriched-v4-20261121` |
| Optional V5 application deployment | `travelsync_poi_metadata_enriched_v5.csv` | `deployment_snapshot_v5.json`, version `busan-enriched-v5-20261125` |

Files are under `src/ourney_travelsync/data/`. Application specifications pin CSV,
operating-info and compressed-manifest hashes. The manifests supply versioned
metadata, schedule records, source evidence, review records and conflict suppression.
Selecting a CSV alone is insufficient. See [deployment setup](deployment_snapshot.md).

V4 contains 74 normalized schedules. V5 contains 73 normalized schedules and 334
provisional schedule records: 88 provide estimated ordinary windows, while 246
retain unresolved conditions without an invented weekly window. These counts are
not counts of independently verified venues. Strict requests reject provisional
estimates; permitted estimates constrain visits and carry uncertainty warnings.
Conflicting source evidence remains suppressed and reviewable.

V5 includes an explicitly authorized in-place hours amendment dated 26 November
2026. Its identifier therefore refers to the amended manifest for new planning
requests, including replanning older V5 recommendations. Saved request/response
rows retain their original content. Reconstruction of the pre-amendment hours
requires the original manifest and private database backup retained outside Git.

## Select V5

Set these in the calling PowerShell session before using the existing setup or
upgrade commands. Pass the actual active version to `--expected-active` on upgrade.

```powershell
$env:TRAVELSYNC_DATASET_SNAPSHOT = '/data/deployment_snapshot_v5.json'
$env:PLACES_FILE = 'travelsync_poi_metadata_enriched_v5.csv'
$env:PLACES_VERSION = 'busan-enriched-v5-20261125'
```

For local Python processes, use the filesystem path to the specification instead
of `/data/`. API and recommender must select matching versions. Prewarm E5 against
that exact catalogue. Ordinary startup validates the installed snapshot without
importing or activating it.

## Compatibility data

The V1 enriched CSV is used by snapshot-preservation regression tests. The V2 core
CSV retains the original feature snapshot and provenance. Demo CSVs and the API's
sample data support explicit development workflows. These files are not substitutes
for a deployment manifest. Source metadata and fetched operating text are not
independent confirmation of current hours or recommendation relevance.
