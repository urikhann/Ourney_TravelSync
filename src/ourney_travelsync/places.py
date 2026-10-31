"""Load the final, conservatively deduplicated CSV without remapping features."""

import csv
from dataclasses import dataclass
from pathlib import Path
from importlib.resources import files

import numpy as np

from .common import FloatArray
from .features import FEATURE_NAMES, validate_features

OFFICIAL_FILENAME = "travelsync_poi_metadata_enriched_v1.csv"
OFFICIAL_SHA256 = "583046906e737845fe5673487a40f79a84401c146f23df516c8c39af94e33eb4"


@dataclass(frozen=True)
class PlaceDataset:
    """Rows, IDs, coordinates and binary features share the CSV row order."""

    records: tuple[dict[str, str], ...]
    place_ids: tuple[str, ...]
    features: FloatArray
    coordinates: FloatArray  # latitude, longitude


def load_places(path: str | Path | None = None) -> PlaceDataset:
    """Validate core fields and preserve every supplied metadata column.

    No description, price, opening hours or dwell-time fields are required.
    Do not merge again: the input already contains the final merged POIs.
    """
    required = {"place_id", "place_name", "category_name", "latitude", "longitude", *FEATURE_NAMES}
    resource = files("ourney_travelsync").joinpath("data", OFFICIAL_FILENAME) if path is None else Path(path)
    with resource.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("CSV header must contain unique column names.")
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Missing CSV columns: {sorted(missing)}")
        records = tuple(reader)
    if any(None in row or any(value is None for value in row.values()) for row in records):
        raise ValueError("CSV rows must match the header width.")
    ids = tuple(row["place_id"] for row in records)
    if any(not value.strip() for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("CSV place_id values must be nonempty and unique.")
    if any(not row["place_name"].strip() for row in records):
        raise ValueError("CSV place_name values must be nonempty.")
    try:
        features = np.asarray([[float(row[name]) for name in FEATURE_NAMES] for row in records], dtype=float).reshape(-1, 10)
        coordinates = np.asarray([[float(row["latitude"]), float(row["longitude"])] for row in records], dtype=float).reshape(-1, 2)
    except (ValueError, TypeError) as exc:
        raise ValueError("CSV features and coordinates must be numeric.") from exc
    validate_features(features, preferences=False)
    if (not np.isfinite(coordinates).all()
            or np.any(np.abs(coordinates[:, 0]) > 90)
            or np.any(np.abs(coordinates[:, 1]) > 180)):
        raise ValueError("CSV coordinates must be finite valid latitude/longitude values.")
    return PlaceDataset(records, ids, features, coordinates)
