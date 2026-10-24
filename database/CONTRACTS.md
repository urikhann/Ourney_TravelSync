# PostgreSQL and recommendation contracts

The supplied baseline SQL is retained as schema version 001. New installations
apply `database/schema.sql`, then migration 002, atomically via `db-init`. Existing
installations use explicit `db-migrate`. No database is initialized on import.

## Immutable data and activation

`poi` uses `(poi_id, dataset_version)` as its primary key. External `place_id` is
unique within a version. The UUID is deterministically derived from external ID;
it is not a matrix index. READY dataset rows, sources, audit records and metadata
are immutable through database triggers. Updates create a new BUILDING dataset,
verify its row count, and publish READY. `dataset_head` is a separate active pointer.

Full CSV imports preserve approved USER records from the parent dataset. Missing
API records disappear only from the new version, not from history. Mapping changes
block cloning; reprocessing or a reviewed migration is required. Imports and
approval use the same advisory transaction lock. Activation requires the expected
head and a non-stale parent. CLI import never activates; activation is separate.
Trusted Python APIs retain an explicit activation option. Administrator approval
of a USER submission explicitly publishes a new snapshot atomically, as in the
handoff. Pending submissions cannot supply feature labels. Approved records require
admin evidence and duplicate checks; authentication endpoints remain out of scope.

## Preferences, constraints and run snapshots

Preferences are immutable integer 1-5 vectors in `features.FEATURE_NAMES` order.
New preference versions lock the user row. Constraints lock the group and create
immutable versions. Group membership changes must use the same group-lock contract.

`create_recommendation_run` locks the group/users and captures dataset version,
feature/mapping version, preference versions, constraint ID and config JSON.
`run_preferences` has actual foreign keys and deferred consistency checks against
`member_snapshot`. `get_run_inputs` reads those captured versions, not mutable
latest preferences or the current head. The config stores algorithm version,
NumPy version, candidate depth, Borda depth, output length, threshold and tie seed.
For full replay retain the code commit and lockfile too.

`db.adapter.recommend_database` creates the run and computes from `get_run_inputs`
inside one transaction, saves individual and group outputs, and marks COMPLETED.
An exception rolls back the entire operation, including the newly created run.
It does not create a misleading persisted failure record outside that transaction.
Input adapters sort user/POI UUIDs, validate version membership and feature alignment,
then apply exclusions before candidate generation. Outputs retain DB POI UUIDs.

## Scores and undefined fields (migration 002)

| Field | Meaning |
|---|---|
| individual_scores.score | Mean survey preference over active labels; 1-5 |
| individual_scores.rank | Descending score with seeded identity ties; NULL for excluded POIs |
| individual_scores.eligible | Explicit constraint exclusions passed; not itinerary feasibility |
| group_scores.mean_score | Mean of all captured members' original 1-5 scores |
| group_scores.group_rank | Algorithm selection order |
| group_scores.selection_gain | GFAR marginal coverage gain; NULL for AWM |
| group_scores.group_utility | NULL until an optimizer defines utility |
| group_scores.feasible_dates | NULL until dates are actually checked |

The handoff SQL required normalized 0-1 scores and mandatory gain/utility/dates,
which conflicted with the working recommender. Migration 002 changes score CHECKs
to 1-5 and permits undefined result fields to be NULL. It refuses an existing
nonempty results table: historical result scales cannot safely be guessed. Back up
and review existing results before migrating; do not bypass immutable-result
triggers or silently rescale. Individual scores and group results remain separate.
All eligible member scores must exist and match the group mean before insertion.

## Filtering versus future optimization

Member exclusions use DB `poi_id` UUID strings and exact `category` or
`category_name` strings, unioned across captured members. General recommendation
APIs accept external eligible/excluded place IDs when using CSV datasets. Neither
API interprets user preferences as hard constraints. AWM's threshold is a model
veto, not a guarantee of real-world acceptability.

Daily windows, budget, movement limits and accessibility are captured and
structurally validated, but are not solved by the recommendation adapter.
Opening hours, holiday schedules, price, festival dates and dwell time are absent
from the official CSV. No schedule/routing/budget feasibility or PASSED itinerary
is produced. Existing itinerary persistence functions remain storage adapters for
future trusted optimizer output; they do not validate all domain constraints.

## Transactions and security boundaries

Repository functions accept a psycopg connection and use transactions/savepoints.
The outer `with connect()` commits or rolls back. Result inserts are immutable;
recomputation creates a new run. Database credentials stay server-side and error
output is sanitized. Public APIs must implement actor authentication and group
access checks; these repository functions alone do not authorize end users.

DB commands load `.env` from the current directory, with exported values taking
precedence. Ordinary recommendation imports do not load PostgreSQL, HTTP, pandas
or Streamlit modules. Submission approval additionally requires the ingestion extra.

Use only explicitly configured disposable PostgreSQL for integration tests. The
teammate's reported successful load is history, not validation of this checkout.
Local test status is recorded in `docs/cleanup_report.md`.
