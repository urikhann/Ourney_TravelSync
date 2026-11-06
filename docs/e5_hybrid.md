# E5 hybrid application integration — 2026-11-07

Implemented on `feat/e5-hybrid-recommender`; no merge into main. This document
describes code and verification on that branch, rather than revising the older SRS/SDD.

## Audit and source of truth

The real flow is `api/app/routes/recommendations.py` -> the existing service
`services/recommender/main.py::recommend` -> `prepare_group` -> GFAR. The CLI
and PostgreSQL adapter call the same package pipeline. The previous API/CLI
default was TF-IDF while service/package calls defaulted to binary feature mean.
The API's pinned POI JOIN previously omitted name/category; those fields now
travel with the snapshot. The packaged enriched v3 CSV has 3,570 POIs, 72 columns,
and SHA256 `3e35a4722b71d788d63c9ac1e4b5060c1df931a54cec739d7475389f404ec5e9`.
It matches commit `a80f48a11210c75aeb2ea32581cd55814f529e32` (ignoring Git line endings).

The November 5 source is available in commit `29018ce`, including
`experiments/individual_scoring_20261105/scoring.py`, `run.py`, and `config.json`.
Its source files are absent from this branch's working tree; its ignored local
model and embedding caches remain available. Production code ports its exact
`document`, weighted `semantic`, and percentile `normalize` semantics.

The user supplied the November 7 report's chosen configuration: weighted E5,
beta=1, alpha=.5, full-catalogue tied percentiles. The reported combined-pool
NDCG@10=.690 / willingness@10=80% is exploratory historical evidence, not
statistical superiority or a production acceptance threshold. Its original
E01–E03 workbooks and generated predictions could not be located. No human
labels enter the runtime or the new regression fixture.

## Equations and scales

Keep the ten original survey values in FEATURE_NAMES order. Feature mean is
`F[u,i] = sum(r[u,k] * x[i,k]) / sum(x[i,k])`, independently callable through
`score_individuals` and `calculate_preference_score`. It remains a 1–5 feature
fit proxy and requires no Transformer. Multiple active features are averaged.
All-zero vectors are unclassified: the existing production input validator
rejects them explicitly rather than assigning a score or assuming eligibility.

`w+ = max(r-3,0)` and `w- = max(3-r,0)`. With unit-normalized interest and POI
embeddings, `C[k,i] = interest[k] dot poi[i]` and
`E = (w+ @ C)/max(sum(w+),1) - beta*(w- @ C)/max(sum(w-),1)`.
Each denominator is per member. Negative-only profiles are valid; neutral
profiles carry no directional evidence. There is no centroid renormalization.

For N>1, `P(s) = (ascending_average_rank(s)-1)/(N-1)` using exact numeric ties.
N=1 and constant vectors map to .5; N=0 stays empty.
`H = (1-alpha)*P(F) + alpha*P(E)`; defaults are alpha=.5 and beta=1.
All normalization uses the complete pinned catalogue **before** eligibility
masking or the member candidate union. Filtering never fits new percentiles.
Scoring version is `e5-weighted-percentile-v1`; text version is `study-description-v1`.
Nonfinite/out-of-range alpha and nonfinite/negative beta are rejected.

Hybrid values are relative relevance, with `score_scale=relative_relevance_0_1`.
They are neither probabilities nor satisfaction ratings. Component values
`feature_score_raw`, `semantic_score_raw`, `feature_score_normalized`,
`semantic_score_normalized`, `hybrid_score`, method, and version remain separate.
Unavailable semantic/hybrid components are JSON null, never invented zeros.

## Text and encoder

Model: `intfloat/multilingual-e5-small`, revision
`614241f622f53c4eeff9890bdc4f31cfecc418b3`, CPU only; no training.
Interest texts are exactly:

`음식 식사`, `카페 커피`, `자연 경관`, `역사 문화유산`, `미술 예술`, `쇼핑`,
`체험 활동`, `오락 놀이`, `휴식`, `축제 행사`.

Each has prefix `query: `. POIs have prefix `passage: ` and the evaluated
space-joined `place_name category_name`, followed by cleaned stored
`description_summary` only when `description_source_url` is nonempty and
`detail_verification_status != CONFLICT`. The experiment uses `category_name`,
not an untested substitution of the newer `detailed_category` field. The
experiment's null-string cleanup and internal spaces are preserved. DB nested
metadata is mapped to those same keys. No hours, menus, or descriptions are invented.
The current catalogue has 2,249 eligible descriptions and 1,321 name/category
fallbacks; each emitted individual row records `description_backed`.

## Caches and operational policy

`e5-cache` prewarms shared NPZ files. They contain canonical sorted POI IDs,
unit-normalized float32 POI/ten-interest embeddings (384 dimensions), exact model
and revision, text version, dataset scoring-content hash, text fingerprint,
generation timestamp, and embedding-byte checksum. The content hash covers
identities, features, coordinates, constructed text, description eligibility and
source/verification fields. Equivalent CSV and DB JOIN inputs share a cache;
unused CSV columns or alternate JSON null spellings do not create false misses.
Actual scoring-text, feature, source, coordinate or ID changes invalidate it.

Cache loads check IDs, metadata, dimensions, norms, finiteness and byte checksum.
The process keeps up to two bundles in memory. Disk writes use a cross-process
directory lock and atomic replacement; threads serialize generation. Lock waits
are bounded (10 seconds by default). A stale lock is not forcibly broken; remove
it only after checking that no prewarming worker is active. Corrupt/missing
files are regenerated when regeneration is enabled, otherwise feature fallback
is reported. User preferences are never written to shared caches.

Normal requests default to `TRAVELSYNC_E5_REGENERATE=0`: they read caches or
fall back, without loading a Transformer. Prewarming explicitly enables
generation. `--download` permits downloading only the pinned checkpoint;
ordinary requests always use local files only. An operator can enable request
regeneration with `TRAVELSYNC_E5_REGENERATE=1`, but initial encoding then adds
unbounded cold CPU latency. Prewarm before serving the demo. Transformer imports
are lazy; feature-only execution and ordinary tests need no model or network.
Model downloads, embeddings, generated builds and local test clusters are ignored.

## Fallback, constraints and aggregation

Model/cache initialization, corrupt output, and per-member semantic errors fall
back to feature mean with fixed reason codes. Invalid preferences and scoring
configuration fail validation; fallback does not hide them. All-neutral members
use feature scoring; negative-only members retain negative semantic evidence.
Each member has requested/effective method and a fallback reason. In a hybrid
request, fallback scores are **full-catalogue feature percentiles**, preserving
feature ranking and comparable relevance scale. Explicit `feature_mean` returns
raw 1–5 values. Mixed groups keep other members' valid hybrid rankings.

Explicit eligible/excluded IDs are masked before the member top-100 candidate
union. The API continues resolving category tags such as raw_fish/hiking to
known POI IDs. Exclusion reasons are preserved, and no score can override a
confirmed exclusion. The service reports `status=exhausted` and a reason for an
empty output; the Python list-returning interface remains compatible.

GFAR itself is unchanged: separate member rankings, seeded identity tie order,
Borda depth 20, greedy selection gains. Group `mean_score` is only a diagnostic
mean on the declared relevance scale. GFAR gain and existing rank-derived
`group_utility` remain distinct. No user profile or embedding is averaged before
scoring. No fairness/satisfaction claim is derived from hybrid values.

Service/CLI AWM comparison requests route to feature mean, threshold 2.5 on 1–5;
explicit TF-IDF AWM and thresholds outside [1,5] are rejected. Direct Python and
captured DB AWM calls reject non-feature scorers instead of silently rerouting.
Plain average comparisons also use feature mean when hybrid was requested.
Legacy TF-IDF remains selectable for GFAR/average, with no cosine misery veto.

This branch has the existing E1 pipeline and stand-in itinerary service, not the
later optimizer branch's complete E2/E3 implementation. Those modules were not
ported or redesigned. Unknown hours/costs retain their prior handling. Verified
downstream compatibility here is the existing itinerary contract and sample
flow, not proof of real routing or E2/E3 feasibility.

## API, CLI, persistence and display

Existing URLs and result list structure remain. New defaults are `e5_hybrid` in
Python, service, API and CLI; `binary` remains an alias for `feature_mean`.
API run bodies accept `individual_method`, optional `alpha`, optional `beta`.
Service options expose the same fields. Existing legacy `binary` requests retain
feature scoring; snapshots lacking name/category can only use whatever real
text they carry. Historical DB configs without a method continue replaying binary.

Responses add `scoring`, `requested_scoring_method`, `score_semantics`, `status`,
and `exhaustion_reason`. Internal service individual lists carry component scores;
the public API retains group results and method/fallback diagnostics without
adding individual raw feature ratings to its public response. Hybrid satisfaction
metrics and display fit percentages are null. UI score components render that
absence as relevance rather than 0% satisfaction. The stand-in optimizer preserves
null group fit rather than adding absent satisfaction values; its agent code is unchanged.

The application already stores complete request/response JSON: no SQLAlchemy
table migration is needed. The separate PostgreSQL package requires additive
`005_e5_score_semantics.sql`, adding individual `scoring_details` and validating
the declared run's scale. Historical scores/config snapshots remain unchanged.
Package DB rows use internal UUID identities, so CSV external-ID caches are not
reused across that identity boundary; without a DB-specific prewarm/operator
regeneration policy, this standalone path transparently uses feature fallback.

## Run and verify

From the repository root (PowerShell):

```powershell
uv sync --locked --extra semantic
# Optional: reuse the existing local HF model folder on this machine.
$env:TRAVELSYNC_E5_MODEL_CACHE = (Resolve-Path experiments/individual_scoring_20261105/.model-cache).Path
uv run --locked --extra semantic ourney-travelsync e5-cache
# On a fresh machine instead: e5-cache --download
uv run --locked --extra semantic ourney-travelsync recommend --diagnostics
uv run --locked --extra semantic ourney-travelsync recommend --individual-method feature_mean
uv pip install -r api/requirements.txt -r services/recommender/requirements.txt
uv run --no-sync uvicorn main:app --app-dir services/recommender --port 8002
```

Point API `RECOMMENDER_URL` to `http://localhost:8002` for local runs. Start the
existing API and web processes per their READMEs; do not point the new default
at the legacy mock recommender. Docker Compose uses the real recommender, installs
the semantic extra, and mounts `output/e5-cache` and `output/e5-models`; prewarm
using the same directories/model cache before serving. Docker image build itself
was not executed in this run.

```powershell
uv run --no-sync pytest tests services/recommender/tests -q
uv run --no-sync pytest api/tests -q
# Requires the preserved local November 5 caches and Git object 29018ce:
uv run --no-sync python scripts/verify_e5_integration.py
# Opt-in actual E5 application check (prewarmed cache required):
$env:TRAVELSYNC_REAL_E5_INTEGRATION = '1'
uv run --no-sync pytest api/tests/test_e5_application.py -q
Remove-Item Env:TRAVELSYNC_REAL_E5_INTEGRATION
# PostgreSQL: explicitly configure a disposable test URL, never the shared DB.
uv run --no-sync pytest integration_tests -q
# Existing package DB only, reviewed explicit migration:
uv run --no-sync ourney-travelsync db-migrate --directory database
```

## Executed verification and limits

See [machine-readable evidence](e5_validation.json). With preserved November 5
embeddings, all 16 synthetic profiles / 3,570 POIs matched exactly: raw semantic
and hybrid max error 0, all rankings, candidate order and GFAR Top-10 equal.
The deterministic small fixture is generated from the recovered original scorer,
not from integrated outputs or human labels. Fresh encoding differed by at most
7.45e-8 per POI embedding and 6.71e-8 per interest embedding. All 16 Top-10 sets
were unchanged, but only 2/16 complete rankings were identical; max hybrid
difference was .00028019053. Do not promise bitwise reproducibility after cache
regeneration or claim exact November 7 participant prediction equivalence.

Executed: 87 core/service tests plus 21 subtests; 48 API tests; 2 disposable
PostgreSQL integration tests, including migration, historical-record preservation
and labeled hybrid fallback; one additional real-E5/full-dataset API execution;
TypeScript build and Vite production build. No tests remain failed. PostgreSQL
initially skipped without a disposable URL, then passed on an isolated temporary
local cluster (stopped after testing). Ordinary automated tests use mock embeddings.
Warnings are pre-existing FastAPI/httpx, SQLite date-adapter and viewer metadata
deprecations. The Docker image and live deployed app were not validated.

Final four-member warm in-process HTTP measurement: median 329.9 ms, p95 459.9 ms
over 10 requests; process peak working set 216.2 MiB. This includes catalogue
fingerprinting, cache lookup, scoring, GFAR and JSON, but excludes API DB/auth,
network transport and cold model loading/encoding. It is not a deployment SLA.

The full API test also measured five warm two-member requests through SQLite
JOIN, in-process HTTP, actual E5 cache, GFAR and run JSON persistence: median
1187.8 ms, p95 1238.6 ms, test-process peak working set 445.7 MiB. Authentication
was overridden and cold encoding/network excluded. The peak includes test setup
and catalogue import, rather than being isolated incremental scorer memory. See
[application measurement](e5_api_validation.json).

The source-backed descriptions are partially reviewed and include uneven
coverage and selection bias. Semantic relevance is not calibrated satisfaction.
Future independent human/group evaluation remains necessary. Next SRS/SDD
revision should capture full-catalogue normalization, score scales/fallbacks,
cache operations, the newly transmitted text fields and null satisfaction display.

The exact created/modified file inventory is recorded in
[implementation changes](e5_changes.md).
