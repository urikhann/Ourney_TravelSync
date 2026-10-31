# TravelSync - Ourney

Different tastes, one itinerary. Inha University CSE capstone, team Ourney.

This repository implements Python group recommendations, Busan POI collection and
cleaning, immutable PostgreSQL dataset/run snapshots, and an optional Streamlit
viewer. Frontend, authentication API, LLM agent, routing and itinerary optimization
are planned, not implemented here.

## Install and run

Use Python 3.13 and `uv`. From the repository root:

```powershell
uv sync --locked
uv run --locked ourney-travelsync --help
uv run --locked ourney-travelsync recommend --algorithm gfar --top-k 10 --tie-seed 0
uv run --locked ourney-travelsync recommend --algorithm awm --users users.json
uv run --locked pytest -q
```

`uv sync` installs the project editable and includes development tools. For core
installation alone use `uv sync --locked --no-dev`. Run commands with the same
extras used at installation because `uv run` synchronizes the requested environment.
`pyproject.toml` is authoritative; `uv.lock` is resolver-generated. No independently
maintained requirements files are needed.

```powershell
uv sync --locked --extra ingestion --extra postgres --extra viewer
uv run --locked --extra viewer streamlit run apps/data_viewer.py
```

Extras: `ingestion` (requests/pandas), `postgres` (psycopg/python-dotenv), `viewer`
(Streamlit/pandas/PostgreSQL), `experiments` (MLflow/DVC). Core requires only NumPy.
Scikit-learn is not a direct dependency; optional MLflow may bring it transitively.
Development tools are in the `dev` group. Existing environments and local experiment
records are not cleanup targets.

`users.json` maps user IDs to ten named preferences from 1 to 5:

```json
{"alice":{"food":4,"cafe":5,"nature":3,"history":2,"art":2,"shopping":3,"activity":3,"entertainment":3,"relax":4,"festival":1}}
```

Without `--users`, the CLI uses two example profiles. `--csv PATH` selects an
external CSV; otherwise it loads the installed official v2 resource independently
of the current working directory. Python use:

```python
from ourney_travelsync import FEATURE_NAMES, load_places
from ourney_travelsync.recommenders import recommend_group

users = {"alice": dict.fromkeys(FEATURE_NAMES, 3)}
results = recommend_group(load_places(), users, top_k=10, tie_seed=0)
```

## Current layout

```text
src/ourney_travelsync/
    cli.py, common.py, features.py, places.py, evaluation.py
    data/             official v2 CSV and provenance
    recommenders/     individual scoring, AWM, GFAR, candidates and ties
    ingestion/        API collection and fixed-input processing
    db/               connection, import, repositories, submissions, run adapter
    viewer.py         optional viewer implementation
apps/data_viewer.py   thin Streamlit entry point
database/            schema, explicit migrations, mapping and contracts
notebooks/compare_recommenders.ipynb
scripts/              thin wrappers, diagnostics and wheel verification
tests/                default offline suite
integration_tests/    opt-in disposable PostgreSQL checks
docs/                 workflows, diagnostics and cleanup report
.github/workflows/tests.yml
```

## Data and recommendation behavior

The authoritative Git-tracked CSV is
`src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v1.csv`: **3,570 records, 72
columns, 3,570 unique place IDs**. SHA256:
`583046906e737845fe5673487a40f79a84401c146f23df516c8c39af94e33eb4`.
Sources: 345 TourAPI, 106 merged and 3,119 Kakao. Its adjacent README records
provenance and the distinction between external place IDs and database UUIDs.
The enriched snapshot preserves the previous 29 core columns and adds 43 metadata
and provenance fields. Loaders retain all fields; database imports store the additions
in `enriched_metadata`. Existing package databases require migration 003 before import.
Existing API databases require an explicit CSV import with the new version
`busan-enriched-v1`; startup seeds only an empty database.

Feature order: `food, cafe, nature, history, art, shopping, activity,
entertainment, relax, festival`. POIs have independent binary labels, at least one
per row. Individual scores average preferences over active labels, on the original
1-5 scale. Candidates are the union of each member's top-N (default 100).

Candidate cutoffs and persisted individual rankings break ties using seeded SHA256
of stable POI identities (default seed 0). Unequal scores retain descending score
order. Candidate output rows use the identity order, making fixed-seed results
independent of CSV ordering. AWM/GFAR themselves break ties by supplied input order;
the standard pipeline supplies the seeded order.

AWM vetoes a POI if any member scores below 2.5 by default, then ranks by mean.
GFAR uses normalized Borda relevance (default depth 20) and greedy coverage gains.
Its score is selection gain, not mean satisfaction. Compare algorithms using
`evaluation.evaluate_satisfaction`, which reads original member scores. Empty
lists have undefined satisfaction. The notebook demonstrates this comparison.
AGREE is unimplemented; see `docs/agree_design.md`.

The pipeline filters explicit eligible/excluded IDs before scoring. Database runs
also filter captured member exclusions by database UUID or exact category/category_name.
Budget, dates, operating hours, accessibility and routing feasibility are **not
enforced**. Undefined utility/feasible-date fields remain SQL NULL. No itinerary is
produced or certified feasible.

## Collection and PostgreSQL

See [data workflows](docs/data_workflows.md) for API collection, JSON replay,
cleaning, deduplication, feature mapping and generated output. Generated data lives
under ignored root `data/`; collection cannot overwrite package source or activate
the database. Live collection is not a reproducible historical snapshot.

Copy `.env.example` to `.env` locally and configure PostgreSQL, or export
`DATABASE_URL`. Database commands load `.env` from the current directory. Never
commit credentials. Verify initialization on an empty disposable database first:

```powershell
uv run --locked --extra postgres ourney-travelsync db-init --directory database
uv run --locked --extra postgres ourney-travelsync db-import src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v1.csv --validate-only
uv run --locked --extra postgres ourney-travelsync db-import src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v1.csv
uv run --locked --extra postgres ourney-travelsync db-check
uv run --locked --extra postgres ourney-travelsync db-activate NEW_DATASET_UUID
uv run --locked --extra postgres ourney-travelsync db-recommend GROUP_UUID CONSTRAINT_UUID --algorithm gfar
```

Groups, preferences and constraints must already exist through repository functions;
there is no public authentication or preference-entry API. `db-recommend` creates a
run, reads its captured inputs, and saves separate individual/group result fields
in one transaction. Later preference/head changes do not alter its inputs. See
[DB contracts](database/CONTRACTS.md) and [CSV mapping](database/CSV_MAPPING.md).

For later head changes use `db-activate NEW --expected-head CURRENT`. Import alone
does not activate. Existing schemas require explicit `db-migrate --directory
database`; initialization refuses nonempty schemas. Migration 002 corrects the
handoff's incompatible 0-1 score checks to 1-5 and permits undefined result fields.
It refuses databases with historical recommendation results, which need manual
review instead of automatic rescaling. No existing database was migrated by this
cleanup. Installed wheels require an explicit checkout/source-distribution
`database/` directory for schema operations.

## Verification and limits

```powershell
uv run --locked pytest -q
uv run --locked --extra ingestion --extra postgres --extra viewer pytest -q
uv run --locked python scripts/tie_diagnostics.py
uv build
```

Default tests are offline and need neither API keys nor PostgreSQL. To opt into DB
checks, explicitly export `TRAVELSYNC_TEST_DATABASE_URL` for a disposable database,
then run `uv run --extra postgres pytest -q integration_tests`. This creates and
drops a uniquely named test schema. Never point it at a shared database.
CI defines offline, wheel and disposable PostgreSQL jobs. Local results and the
review-finding resolution map are in [the cleanup report](docs/cleanup_report.md).

Rule labels, tied category scores and narrow example profiles limit evaluation.
No quality or fairness improvement is inferred from source-composition diagnostics.
Data-provider terms still apply. A project license has not been selected by the team.
