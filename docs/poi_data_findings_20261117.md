# POI data findings and daily collection

Evidence date: 17 November 2026, Asia/Seoul.

## Packaged dataset and import behavior

The configured V3 metadata and operating-info files each contain 3,570 unique, matching place IDs. A disposable database run of the real importers accepted every POI, stored 1,903 weekly-hour rows for 296 places, and 9,366 dated exceptions for 1,338 places. Another 1,936 places had no imported schedule. All accepted snapshots cover only 26 October–1 November 2026.

Only 296 places (8.3%) therefore have recurring schedules after a fresh import, and 256 combine description content with those schedules. Of 2,252 populated descriptions, 1,250 are menu-based, 226 are facility facts, and 776 are other descriptive text. These are content/provenance categories, not independent factual verification.

The audit found 9 recorded metadata matching conflicts, 98 ambiguous operating records, 165 recurring schedules with incomplete closure policy, 32 recurring records with unmodeled breaks, and 17 with unmodeled last orders. Forty-five schedules infer all-day access from prose. Existing-version startup does not automatically re-import changed hours. The inspected API optimization path explicitly disables opening-hours enforcement. No setting was changed during collection.

These findings establish local CSV/code behavior, not the contents or behavior of a deployed database. Original inputs are retained unchanged.

## Catalogue-wide collection results

The new TourAPI region parameter `lDongRegnCd=26` returned 2,235 Busan records, compared with 112 from the earlier legacy `areaCode=6` query. Busan Food returned 536 records. The separate Busan Attraction service rejected the available key, so its coverage remains untested.

The bulk pass checked every existing POI and retrieved hours text for 645 places, including 547 previously unknown/expired records. There are 431 preliminary regular-schedule candidates and 35 POIs with differing extracted time ranges. Keyword fallback remains unresolved for 1,952 places in the bulk inventory after the daily search allowance was reached. These are evidence candidates, not newly verified planning-eligible places. Nothing was imported.

The user confirmed a 1,000-call daily limit for **each** TourAPI operation, including searchKeyword2, detailIntro2 and detailCommon2. Collecting more descriptions uses its own remaining operation allowance; it does not increase the search allowance.

Full saved findings and source evidence:

- `output/data_audit_20261117/`: complete audit, per-place results, validation and source hashes.
- `output/bulk_hours_20261117/`: catalogue-wide hours inventory, source matches, raw redacted responses, resumable keyword state and collection report.

## Daily collector

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B scripts/collect_poi_enrichment_daily.py
```

For offline validation using saved evidence:

```powershell
.\.venv\Scripts\python.exe -B scripts/collect_poi_enrichment_daily.py --offline --max-new-calls 0
```

The script reads existing `.env` keys without printing them. It writes only to ignored `output/daily_poi_enrichment/`. It resumes prior keyword discoveries, reuses hashed source responses, reserves quota before each request, counts the pilot/bulk/daily journals together, applies Korean calendar-day resets, stops at 1,000 calls per operation and provider quota/authorization errors, and prevents overlapping local runs. The per-run ceiling is 3,000 new requests. External consumers of the same key may reduce the remaining provider quota; provider limits always take precedence.

Hours responses are cached for 7 days, descriptions for 30 days, catalogue pages for 1 day, and no-match keyword responses for 30 days. Deferred searches consume later daily allowances. A completed queue is maintained rather than repeatedly spending calls on the same no-match places. Cached evidence keeps its original retrieval timestamp. Busan authorization is retried on later runs so newly enabled access can recover.

The collector also retrieves `detailCommon2` descriptions for matched places and Busan `ITEMCNTNTS` text. Validation checks response shape, conservative place identity, meaningful content presence, preliminary clock/closure patterns, differing source ranges, complete one-row-per-POI output, and unchanged packaged CSV hashes. It does not certify factual accuracy or invent hours from generic all-day prose. Uncertain schedules remain reviewable.

Outputs include `all_pois_latest.csv`, `evidence_latest.csv`, `validated_evidence_latest.csv`, `review_queue_latest.csv`, `latest_summary.json`, `REPORT_latest.md`, and dated archives in `runs/`. Original raw provider responses and credential-free request metadata are preserved. Validated evidence is separate from production eligibility; there is no automatic import, activation, parser change or enforcement change.

## Schedule and reports

The Codex daily automation is intended for 00:10 Asia/Seoul, shortly after the Korean daily quota boundary. It runs this script, waits for completion, reads the saved report, and reports new/cumulative hours and description coverage, validation outcomes, remaining queues, quota/access problems, and output links. It requires this local computer and Codex to be available. If a run is missed, cached queues remain resumable at the next execution.

Enable the separate Busan Attraction API on the public-data key to add that source. Quota increases are a separate account action. No credentials belong in source control.

Official documentation: [TourAPI](https://www.data.go.kr/data/15101578/openapi.do), [Busan Food](https://www.data.go.kr/data/15063472/openapi.do), [Busan Attraction](https://www.data.go.kr/data/15063481/openapi.do).
