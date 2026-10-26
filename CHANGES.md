# v0.4 — aligned with TS SRS 1.1 / SDD 1.1 (2026-10-11)

**Before running:** delete your old database once — the tables changed.
`rm api/travelsync.db` (Docker: `docker compose down -v`). Then start as usual; the 3,591 places import automatically.

- Survey: the 10 features in the fixed order, whole numbers 1–5 only (0, 6, 4.5, "4", missing → 422). Every save is a new version.
- Personal exclusions (no hiking / raw fish / bars) are a separate input: `PUT /trips/{id}/exclusions/me`. Walking and pace are gone.
- Real data: `data/places/travelsync_final_places.csv` + `python -m app.import_places` (checks every row, all-or-nothing,
  dataset versions DRAFT → ACTIVE → ARCHIVED; places keyed by id + version). Unknown price/hours stay NULL, never 0.
- Mock recommender = the SDD: P = Σ w·x (0–1) and GFAR, `services/mocks/group_rec.py`. `method: "average" | "awm"` for comparison.
  Runs store the dataset version, feature schema version and each member's preference version.
- Limits: groups of up to 10, trips of 1–3 days.
- Web: 10-feature survey (English + Korean labels), GFAR vs average switch, % fit, Korean place names, photos when available,
  address/phone/Kakao Map link, "price unknown" instead of free.
- Contracts updated (`contracts/README.md`). Tests: 23 (incl. the SDD fixtures: score 10/30, GFAR A,C,B).
