"""Keep generated output away from installed source and the official snapshot."""
from pathlib import Path


def validate_output_location(path):
    resolved = Path(path).resolve()
    package = Path(__file__).resolve().parents[1]
    if resolved == package or package in resolved.parents or resolved in package.parents:
        raise ValueError("OUTPUT_OVERLAPS_PACKAGE: choose data/generated outside package source")
    return resolved
