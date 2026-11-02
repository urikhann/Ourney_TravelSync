# Default individual recommendation: Option C

The `tf-idf` branch started at main commit `6e49e68`; it already existed at that
commit with no tracked differences when implementation began. No existing
untracked experiment artifacts were replaced. The default source remains the
packaged `travelsync_poi_metadata_enriched_v1.csv` (3,570 POIs), loaded by
`load_places()` without changing source data.
Of these rows, 3,545 have nonempty allowed text, 1,835 have description summaries,
1,630 have food type, and 477 have representative menus after cleaning.
All 25 empty documents remain eligible when constraints permit and score zero.

## Differences identified before implementation

The prior experiment is preserved on branch `exp`, at
`notebooks/experiments/enriched_poi_round1_20261028/{prepare,run}.py`.

| Aspect | Previous experiment | Main before this change | Implemented configuration |
|---|---|---|---|
| Scorer | Custom `Lexical` class, smoothed IDF, raw counts, L2, cosine | Binary-feature preference average, 1-5 | Requested scikit-learn character TF-IDF, raw cosine 0-1 |
| Character grams | 2/3 characters within each whitespace token | None | `analyzer="char"`, including spaces and boundaries |
| Text fields | detailed_category, food_type, representative_menu, activity_type, description_summary | Metadata retained but unused | Exactly those five fields, no weights or aliases |
| Corpus | Frozen 19 eligible demo POIs; 12 explicit Korean queries; within-feature-group evaluation | Full enriched catalogue, numeric ratings | Request-eligible POIs only; optional member preference text |
| Activity column | `activity_type` in demo source | `representative_activity` in packaged source | Missing `activity_type` contributes empty text; no substitution |
| Tie order | Place ID fallback | Seeded identity hash | Existing production tie policy retained |

Consequently this implements the explicit requested vectorizer settings, but
cannot reproduce old numeric results exactly: scikit-learn character analysis
differs from the custom word-local grams, and the source corpus has changed.
There is no claim of improved relevance or user satisfaction.

## Scoring contract

Each document joins nonempty cleaned values from the five fields in the stated
order with one space. Cleaning matches the experiment: NFKC normalization,
lowercasing, replace characters outside Korean syllables/ASCII letters/digits/
whitespace with spaces, then collapse whitespace. Missing keys, None, NaN,
empty strings, NULL, N/A, NA, UNKNOWN and NOT_APPLICABLE contribute no text.
No place names, selection labels, URLs, ratings, or other metadata enter scoring.

```python
TfidfVectorizer(
    analyzer="char", ngram_range=(2, 3), smooth_idf=True, norm="l2"
)
```

Other scikit-learn defaults remain unchanged: raw term counts (`binary=False`,
`sublinear_tf=False`), `use_idf=True`, `min_df=1`, `max_df=1.0`, no stop words,
no maximum feature count. `scikit-learn==1.9.1` is pinned in pyproject.toml and
uv.lock, retaining the version already present in main's lockfile.

Eligibility/exclusions are applied before fitting vocabulary and IDF. User text
is transformed with the same fitted vectorizer; queries never enter the fit.
Scores use scikit-learn cosine similarity, clipped only to the mathematical
0-1 range for floating-point roundoff. Empty/OOV queries or documents score zero;
an empty corpus or corpus with no 2/3-character grams returns a correctly shaped
zero matrix. IDF is refitted for each eligible corpus rather than cached globally.

`prepare_group()` and `recommend_group()` default to `individual_method="tfidf"`
and accept `user_queries={user_id: text}` alongside the existing ten-key numeric
preferences. Query keys, when supplied, must match all member IDs. Omitted text
is empty, as requested; all-zero scores use the existing seeded tie policy and
do not establish relevance. Numeric preferences remain validated and preserved
as features, but do not affect TF-IDF. The original `score_individuals()` and
`calculate_preference_score()` remain explicit binary baseline helpers;
historical callers may pass `individual_method="binary"` to the pipeline.

All member scores remain aligned through the existing top-N union and GFAR.
GFAR, Borda depth, tie policy, E1/E2/E3 filtering, optimizer logic and the
rank-based group utility consumed by itinerary optimization are unchanged.
Downstream feasibility checks remain responsible for E2/E3; this scorer does
not assert hours, cost, accessibility or itinerary feasibility.

## Interfaces and storage

The web preference form saves optional `preference_text` with each immutable
preference version. The API forwards it in the captured recommendation request;
the service accepts it on each member and declares `score_scale="0-1"`.
Historical 1-5 response conversion remains supported. Existing numeric ratings
are still accepted. No query text is invented from them.

CLI usage:

```powershell
uv sync --locked --cache-dir .uv-cache
# users.json: existing user-ID -> ten-feature ratings mapping
# queries.json: same user IDs -> preference strings, for example "바닷가 산책"
uv run --locked ourney-travelsync recommend --users users.json --queries queries.json
```

For the package PostgreSQL path, migration `004_tfidf.sql` adds preference text
and permits both historical 1-5 results and new 0-1 results without rescaling old
rows. Apply explicitly via `db-migrate` after reviewing a disposable database.
No live database migration is run as part of this implementation. New CLI run
configs record `individual_method`, score scale and scikit-learn version.
Captured historical configs without `individual_method` replay with the binary
scorer. Excluded POIs in TF-IDF DB results have zero scores, no rank, and
`eligible=False`; their text never influences vocabulary or IDF.

The API follows its existing additive startup-column compatibility pattern to
add empty preference text to existing API databases. Existing rows are preserved.
Tests run only against the API's dedicated SQLite test database.

TF-IDF has no calibrated AWM misery threshold. The service and CLI default to
zero (no veto); explicit thresholds are still honored. The AWM class and its
historical 2.5 default remain unchanged. Legacy response metric names containing
`satisfaction` now carry mean cosine, not evidence of satisfaction.

## Reproducible validation

From the repository root (PowerShell):

```powershell
.venv/Scripts/python.exe -m pytest tests integration_tests -q -p no:cacheprovider --basetemp .pytest-tfidf-unit
$env:PYTHONPATH='services/recommender'
.venv/Scripts/python.exe -m pytest services/recommender/tests -q -p no:cacheprovider --basetemp .pytest-tfidf-service
Remove-Item Env:PYTHONPATH
.venv/Scripts/python.exe -m pytest api/tests -q -p no:cacheprovider --basetemp .pytest-tfidf-api
```

Use a fresh basetemp directory for repeated Windows runs if cleanup permissions
prevent reuse. PostgreSQL tests require an explicitly disposable
`TRAVELSYNC_TEST_DATABASE_URL` and isolate their schema.

Tests cover a direct scikit-learn oracle, character grams across spaces,
repeatability, query isolation, strict field allowlisting, missing/OOV/empty
data, eligible-only fitting, member/POI alignment, GFAR score persistence, HTTP
query propagation and preference-text versioning. Existing API flow tests cover
the unchanged exclusion and itinerary interfaces.

Validation completed on 2026-10-31:

| Check | Result |
|---|---|
| Package and CLI tests | 52 passed; 21 subtests passed |
| Recommender HTTP service | 13 passed |
| API tests, including preference text and additive column compatibility | 28 passed |
| PostgreSQL integration | 1 skipped; no disposable test database configured |
| Web `npm run build` | TypeScript and Vite production build passed |
| `git diff --check` | Passed |
| Enriched dataset SHA256 | Unchanged: `583046906e737845fe5673487a40f79a84401c146f23df516c8c39af94e33eb4` |

GFAR, tie handling, constraint/optimizer source files and official data have no
diff against main. Python checks emitted existing dependency deprecation warnings.
The web build used checksum-verified portable Node 22.23.4 under ignored
`data/generated/tfidf/tooling`, since Node/npm were absent from PATH.
