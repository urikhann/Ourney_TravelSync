"""Make a small demo place list: only the places named in the data teammate's demo-candidate file.

    cd api
    python -m app.make_demo_pool ../src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v1.csv \\
        ../src/ourney_travelsync/data/travelsync_demo_candidates_missing_info.csv \\
        ../src/ourney_travelsync/data/travelsync_demo40.csv

The output has exactly the same columns as the full file, so it can be
  1. imported as its own dataset version:  python -m app.import_places <out.csv> --version busan-demo40-v1
  2. given to the recommender service:     PLACES_CSV=<out.csv> DATASET_VERSION=busan-demo40-v1
Switch back to all places with:            python -m app.activate_version busan-enriched-v1
"""
import argparse
import csv
from pathlib import Path


def demo_ids(path: str | Path) -> list[str]:
    """place_id column of the candidate file, in order, without repeats (the file has one row per missing field)."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(dict.fromkeys((r.get("place_id") or "").strip() for r in csv.DictReader(fh) if (r.get("place_id") or "").strip()))


def make_pool(places_csv: str | Path, candidates_csv: str | Path, out_csv: str | Path) -> tuple[int, list[str]]:
    ids = demo_ids(candidates_csv)
    wanted = set(ids)
    with open(places_csv, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        rows = [r for r in reader if r["place_id"].strip() in wanted]
    found = {r["place_id"].strip() for r in rows}
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return len(rows), [i for i in ids if i not in found]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("places_csv")
    ap.add_argument("candidates_csv")
    ap.add_argument("out_csv")
    a = ap.parse_args()
    n, missing = make_pool(a.places_csv, a.candidates_csv, a.out_csv)
    print(f"Wrote {n} places to {a.out_csv}")
    if missing:
        print(f"Not found in the places file ({len(missing)}): {', '.join(missing)}")
