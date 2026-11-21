"""Verify exact deployment artifact bytes in a checkout and optionally its Git ref.

Uses only the standard library so CI can run it before installing dependencies.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DATA = Path("src/ourney_travelsync/data")
SPEC = DATA / "deployment_snapshot.json"


def verify(root=ROOT, git_ref=None):
    def read(relative):
        if git_ref is None:
            return (root / relative).read_bytes()
        return subprocess.check_output(["git", "show", f"{git_ref}:{relative.as_posix()}"], cwd=root)

    spec = json.loads(read(SPEC))
    if spec.get("schema_version") != 1:
        raise ValueError("unsupported deployment snapshot schema")
    hashes = spec["sha256"]
    for key in ("csv", "operating_csv", "manifest"):
        name = spec[key]
        if name not in hashes:
            raise ValueError(f"deployment artifact has no hash: {name}")
    for name, expected in hashes.items():
        for label, content in [("stored", read(DATA / name)), ("checkout", (root / DATA / name).read_bytes())]:
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f"deployment artifact {label} hash mismatch: {name}")
    rows = list(csv.DictReader(io.StringIO(read(DATA / spec["csv"]).decode("utf-8-sig"), newline="")))
    manifest = json.loads(gzip.decompress(read(DATA / spec["manifest"])))
    ids = [r["place_id"] for r in rows]
    if (not rows or len(ids) != len(set(ids))
            or any(r["dataset_version"] != spec["dataset_version"] for r in rows)
            or manifest["dataset_version"] != spec["dataset_version"]
            or set(ids) != set(manifest["payload"]["metadata"])):
        raise ValueError("deployment catalogue/manifest version or identity mismatch")
    return {"dataset_version": spec["dataset_version"], "places":len(rows),
            "artifacts":len(hashes), "git_ref":git_ref}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-ref", help="also verify bytes stored at this Git ref, e.g. HEAD")
    args = parser.parse_args()
    print(json.dumps(verify(git_ref=args.git_ref)))
