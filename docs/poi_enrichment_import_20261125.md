# Busan POI snapshot · 25 November 2026

26 November amendment: the 334 preliminary entries are now structured provisional
schedule records in this same V5. Of these, 88 supply estimated ordinary windows
and 246 retain unresolved conditions. See the [in-place import report](v5_provisional_schedules_20261126.md).

`busan-enriched-v5-20261125` carries forward the 3,570 POIs, their existing CSV
columns, features and baseline operating data. The deployment manifest uses the
existing versioned evidence, review and normalized-hours tables. Source hashes
and retrieval timestamps identify the daily collection and the additional extracts.

## Daily schedule candidates

| Disposition | POIs |
|---|---:|
| Carried forward as normalized weekly schedules | 73 |
| Imported as preliminary schedule evidence | 334 |
| Excluded for a time-range disagreement or an unresolved identity | 42 |
| Total screened | 449 |

The 42 exclusions comprise 38 daily source-time disagreements, three identity
conflicts and one disagreement found against the maritime feed. For 화명수상레포츠타운
(`kakao:20326447`), the daily source reports 08:30–18:00 and the maritime feed
reports 08:00–19:00. V5 holds both observations for review and suppresses the
weekly schedule for that POI. The V4 schedule remains part of the V4 snapshot.

Preliminary entries include conditional or seasonal rules, unknown closure
policies, breaks, last orders and admission cutoffs. Their source text is
stored with the corresponding review reasons. Only the 73 normalized entries
from this batch are usable as weekly constraints. The existing dated hours and
exception precedence still apply.

## Additional source records

| Source | Rows checked | Records linked to catalogue POIs |
|---|---:|---:|
| Busan maritime experiences, Korean | 133 | 11 |
| Busan maritime experiences, English | 119 | 9 |
| Busan cultural facilities | 148 | 10 |
| Busan walking routes | 56 | 7 |
| Busan Food, live gap check | 539 | 7 |
| Busan Attractions, live gap check | 213 | 1 |

The seven walking routes have eight matching endpoint links. The linked
records produce 46 evidence rows across 31 POIs. Maritime matches require a
unique exact name and nearby coordinates. Cultural facilities and route
endpoints require a unique exact name and address. For duplicated Busan Food
names at the same location, the address also has to agree. Unlinked extracts
remain under `data/raw`. Cultural facility counts and route details are source
evidence. The drone show at Gwangalli Beach is retained as an event at the
venue, with its performance schedule kept distinct from beach hours.

The added sources supplied eight descriptions missing from that day's
collection. Two filled empty production descriptions and six supplemented
existing descriptions. They supplied reported hours for seven POIs missing
from that day's collection. Six had no detected time-range disagreement and
one had competing time ranges. All seven remain in operating-hours review.
The Foodie Tour operation rejected the configured credential.

Cultural facility data is attributed to the Korea Culture Information Service
Agency under KOGL Type 1. The facility feed was published in 2024 and contains
2023 reference data. [Cultural facility source](https://www.data.go.kr/data/15125097/openapi.do)

## Select the snapshot in Compose

Use the existing explicit setup or upgrade procedure. Set these variables in
the calling shell or the deployment environment before starting services.
For an upgrade, pass the actual active dataset to `--expected-active`.

```powershell
$env:TRAVELSYNC_DATASET_SNAPSHOT = '/data/deployment_snapshot_v5.json'
$env:PLACES_FILE = 'travelsync_poi_metadata_enriched_v5.csv'
$env:PLACES_VERSION = 'busan-enriched-v5-20261125'
```

Follow the [activation, rollback and E5 prewarm steps](deployment_snapshot.md).
For a local Python process, set `TRAVELSYNC_DATASET_SNAPSHOT` to the filesystem
path to `deployment_snapshot_v5.json`.
