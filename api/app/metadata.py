"""Enriched place metadata from the data pipeline (travelsync_poi_metadata_enriched_v1.csv).

The CSV writes a missing value as the text "NULL". In the app, missing must be a real empty value (None),
so the screens never show the word "NULL" and nothing unknown is mistaken for a real value.
"""

MISSING = {"", "NULL", "null", "None", "N/A", "n/a"}


def clean_value(v):
    if v is None:
        return None
    if isinstance(v, str) and v.strip() in MISSING:
        return None
    return v


def clean_meta(meta: dict | None) -> dict:
    """{"description_summary": "NULL", ...} -> {"description_summary": None, ...}"""
    return {k: clean_value(v) for k, v in (meta or {}).items()}
