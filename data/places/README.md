# Place data (owner: data pipeline)

`travelsync_final_places.csv` — output of `travelsync_pipeline_rule_v2` (TourAPI + Kakao Local, Busan).
3,591 places · 29 columns · 10 binary features in the fixed order
`food, cafe, nature, history, art, shopping, activity, entertainment, relax, festival`.

How the backend uses it
- Empty database → imported automatically on startup as dataset version `busan-csv-1` (see `api/app/seed.py`).
- New file from the pipeline → import it as a **new version** (old runs keep their version):

```bash
cd api && source .venv/bin/activate
python -m app.import_places ../data/places/travelsync_final_places.csv --version busan-2026-10-v2 --mapping rule_v2
```

The import refuses the whole file (and changes nothing) if any row has an empty id/name, coordinates outside Busan,
a feature that is not exactly 0 or 1, or all 10 features 0.

Not in the data yet (stay NULL / UNKNOWN, never 0): opening hours, closed days, price, description.
Stay time is ESTIMATED from the main feature (`stay-rule-1`). English names are not available.
