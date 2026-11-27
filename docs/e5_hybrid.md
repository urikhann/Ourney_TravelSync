# E5 hybrid scoring

E5 hybrid is the accepted product recommender. For the deployed enriched snapshot,
use [deployment setup and prewarming](deployment_snapshot.md); the generic CLI
examples below may select a different packaged catalogue unless `--csv` is explicit.
Budget enforcement is excluded from product scope.

The API, recommender service, CLI, and PostgreSQL adapter share the package
scoring pipeline. Each member receives a separate relevance vector before GFAR
aggregation. The pinned POI snapshot supplies names, categories, features, and
available source-backed descriptions.

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
`detail_verification_status != CONFLICT`. Scoring uses `category_name`,
not `detailed_category`. Null-string cleanup and internal spaces are preserved. DB nested
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
unbounded cold CPU latency. Prewarm before serving requests. Transformer imports
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

Service/CLI AWM comparison requests route to feature mean, threshold 2.0 on 1–5;
explicit TF-IDF AWM and thresholds outside [1,5] are rejected. Direct Python and
captured DB AWM calls reject non-feature scorers instead of silently rerouting.
Plain average comparisons also use feature mean when hybrid was requested.
Legacy TF-IDF remains selectable for GFAR/average, with no cosine misery veto.

Itinerary feasibility is checked separately by the optimizer and the API's
independent E3 validation. Scoring does not establish route or operating availability.
See [planning rules](planning_rules.md).

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
absence as relevance rather than 0% satisfaction. Itinerary group-fit fields
remain null for relative relevance scores.

The application stores complete request/response JSON: no SQLAlchemy
table migration is needed. The separate PostgreSQL package requires additive
`005_e5_score_semantics.sql`, adding individual `scoring_details` and validating
the declared run's scale. Historical scores/config snapshots remain unchanged.
Package DB rows use internal UUID identities, so CSV external-ID caches are not
reused across that identity boundary; without a DB-specific prewarm/operator
regeneration policy, this standalone path transparently uses feature fallback.

## Run and verify

From the repository root (PowerShell):

```powershell
uv sync --locked --extra semantic --extra optimizer
uv run --no-sync ourney-travelsync e5-cache --download
uv run --no-sync ourney-travelsync recommend --diagnostics
uv run --no-sync ourney-travelsync recommend --individual-method feature_mean
uv pip install -r api/requirements.txt -r services/recommender/requirements.txt -r services/planning_data/requirements.txt
uv run --no-sync uvicorn main:app --app-dir services/recommender --port 8002
```

Point API `RECOMMENDER_URL` to `http://localhost:8002` for local runs. Start the
API and web processes using their setup instructions. Point the API at this
service rather than the mock recommender. Docker Compose uses the real recommender, installs
the semantic extra, and mounts `output/e5-cache` and `output/e5-models`; prewarm
using the same directories/model cache before serving.

```powershell
uv run --no-sync pytest tests services/recommender/tests -q
uv run --no-sync pytest api/tests -q
# Opt-in actual E5 application check (prewarmed cache required):
$env:TRAVELSYNC_REAL_E5_INTEGRATION = '1'
uv run --no-sync pytest api/tests/test_e5_application.py -q
Remove-Item Env:TRAVELSYNC_REAL_E5_INTEGRATION
# PostgreSQL: explicitly configure a disposable test URL, never the shared DB.
uv run --no-sync pytest integration_tests -q
# Existing package DB only, reviewed explicit migration:
uv run --no-sync ourney-travelsync db-migrate --directory database
```

## Reproducibility and limits

Retain the pinned model revision, embedding cache, code, and lockfile for replay.
Regenerating embeddings can reorder close scores across runtime environments.
See [numerical compatibility](e5_changes.md) for frozen-reference tolerances.

Source-backed descriptions have uneven coverage and are not independent venue
verification. Hybrid relevance is not calibrated satisfaction. Use independent
human judgments to assess recommendation quality.
