"""Shared deployment identity; independent of optional API/database dependencies."""
import gzip
import hashlib
import json
import os
from pathlib import Path

DATA = Path(__file__).parent / "data"
SNAPSHOT = Path(os.getenv("TRAVELSYNC_DATASET_SNAPSHOT", str(DATA / "deployment_snapshot.json")))

def snapshot():
    spec = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if spec.get("schema_version") != 1:
        raise ValueError("unsupported deployment snapshot schema")
    for name, expected in spec["sha256"].items():
        path = SNAPSHOT.parent / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"deployment artifact missing or hash mismatch: {name}")
    return spec

def manifest(spec):
    return json.loads(gzip.decompress((SNAPSHOT.parent / spec["manifest"]).read_bytes()))

def configured():
    spec = snapshot()
    version = os.getenv("PLACES_VERSION", spec["dataset_version"])
    if version != spec["dataset_version"] or os.getenv("DATASET_VERSION", version) != version:
        raise ValueError("deployment dataset version mismatch")
    path = Path(os.getenv("PLACES_CSV") or str(SNAPSHOT.parent / spec["csv"]))
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec["sha256"][spec["csv"]]:
        raise ValueError("deployment CSV content mismatch")
    return spec, path
