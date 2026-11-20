# POI enrichment

Collection retrieves source evidence for hours and descriptions. Normalization
and explicit database import are separate steps; retrieved text is not a verified
scheduling fact.

## Run the collector

From the repository root:

```powershell
uv sync --locked --extra ingestion
uv run --locked --extra ingestion python scripts/collect_poi_enrichment_daily.py
```

To validate saved evidence without new provider calls:

```powershell
uv run --locked --extra ingestion python scripts/collect_poi_enrichment_daily.py --offline --max-new-calls 0
```

The script reads credentials from the local `.env` and exported environment. It
writes to ignored `output/daily_poi_enrichment/`, resumes keyword discoveries,
and preserves redacted provider responses with hashes and retrieval timestamps.

## Quotas and caching

The local guard limits each TourAPI operation to 1,000 attempts per Korean
calendar day and each run to 3,000 new requests. It reserves an attempt before
sending it, counts shared collection journals, stops on quota/authorization
errors, and prevents overlapping local runs. Other users of the same key can
consume provider quota; the provider's limits take precedence.

Cache lifetimes are seven days for hours, thirty days for descriptions and
no-match searches, and one day for catalogue pages. Cached evidence keeps its
original timestamp. Deferred searches resume when quota is available; completed
no-match queues are retained. Busan authorization is retried on later runs.

TourAPI uses `lDongRegnCd=26` for Busan. Description sources include
`detailCommon2` and Busan `ITEMCNTNTS`. Busan Attraction requires separate API
authorization on the public-data key.

## Validation and output

Checks cover response shape, conservative place identity, meaningful text,
preliminary clock/closure patterns, conflicting time ranges, one output row per
catalogue POI, and unchanged packaged input hashes. Generic all-day prose is not
converted into confirmed hours. Breaks, last admission/orders, seasonal rules,
and source disagreements require review.

Outputs include `all_pois_latest.csv`, `evidence_latest.csv`,
`validated_evidence_latest.csv`, `review_queue_latest.csv`, `latest_summary.json`,
`REPORT_latest.md`, and archives in `runs/`. Raw source evidence and request
metadata omit credentials. Use [normalized schedule import](normalized_operating_hours.md)
to review and apply supported schedules.

The packaged operating file contains both recurring schedules and dated
snapshots. A snapshot applies only to its recorded dates. Startup does not
re-import changed hours into an existing dataset version.

Provider references: [TourAPI](https://www.data.go.kr/data/15101578/openapi.do),
[Busan Food](https://www.data.go.kr/data/15063472/openapi.do),
[Busan Attraction](https://www.data.go.kr/data/15063481/openapi.do).
