# E5 scoring compatibility

E5 hybrid scores are relative relevance on [0,1]. Explicit `feature_mean` and
`binary` requests use the 1–5 preference scale; `tfidf` uses cosine similarity.
Consumers must read `score_scale` before interpreting values or applying thresholds.
Missing satisfaction and fit values remain null.

AWM requires feature scores and a threshold on the same scale. Service and CLI
comparison requests select feature mean; direct package/database calls reject
incompatible scoring methods. GFAR consumes separate member rankings and retains
its own Borda relevance, selection gain, and rank-derived utility.

Snapshots without a scoring method replay using binary feature scoring. Saved
scores and configurations are immutable. The standalone PostgreSQL schema uses
`005_e5_score_semantics.sql` for scoring details and scale validation; the API
stores complete request/response JSON in its own schema.

## Numerical reproducibility

Frozen-reference tests compare raw semantic scores with one float32 epsilon of
absolute tolerance (`1.1920929e-7`, zero relative tolerance). Feature, normalized,
and hybrid values use `1e-14`; semantic and hybrid ranking equality are also checked.
Tests exercise float32 and higher-precision accumulation. NumPy/OpenBLAS selects
its native CPU kernel automatically.

Regenerated embeddings can differ slightly across environments and reorder near
ties. Retain the embedding cache, model revision, code, and lockfile for replay.
See [scoring equations, cache configuration, and fallback](e5_hybrid.md).
