# Package architecture

The `ourney_travelsync` package separates recommendation logic from optional
collection, persistence, and visualization dependencies.

| Module | Responsibility |
|---|---|
| `features.py`, `data_schema.py` | Shared feature order and CSV columns |
| `places.py` | Packaged-resource and external CSV loading |
| `recommenders/` | Individual scores, candidates, ties, AWM and GFAR |
| `ingestion/` | API collection and fixed-input processing |
| `db/` | Versioned imports, captured run inputs, results and submissions |
| `optimizer/` | Typed planning inputs, eligibility, CP-SAT and final validation |
| `viewer.py` | Optional Streamlit data inspection |

CLI commands and scripts call these modules. Ordinary recommendation imports do
not require HTTP, PostgreSQL, pandas, Streamlit, or Transformer initialization.
Install the matching extras before using those features.

Data loaders retain source columns and use the shared ten-feature order.
Packaged resources load independently of the working directory. Generated
collection output is stored outside package source.

Database runs capture immutable dataset, preference, constraint, and scoring
versions. Individual scores and group outputs are stored separately. Imports
create a reviewable dataset; activation is explicit. Existing databases use
[explicit migrations](../database/migrations/README.md).

For commands, see [installation](../README.md), [data workflows](data_workflows.md),
[database contracts](../database/CONTRACTS.md), and [testing](optimizer_verification.md).
