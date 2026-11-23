"""Read curated breakfast evidence; never infer menu facts from venue names.

Stored in versioned enriched_metadata. Each fact needs KNOWN, source and
verified_at. Categories are explicit breakfast categories, not food/cafe tags.
"""
from datetime import date
import json
import re

from pydantic import StrictBool, ValidationError

from .optimizer.contracts import Fact, Window


def _decoded(value):
    # CSV-backed metadata can preserve JSON objects as cell strings.
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _imported_offering(metadata):
    """Use explicit, field-provenanced menu/category text already imported.

    Broad cafe/food categories, descriptions, names and individual foods (e.g.
    coffee or toast) are deliberately insufficient evidence of breakfast.
    """
    provenance = metadata.get("field_provenance_json") or []
    if isinstance(provenance, str):
        try:
            provenance = json.loads(provenance)
        except (ValueError, TypeError):
            return Fact[StrictBool]()
    if not isinstance(provenance, list):
        return Fact[StrictBool]()
    for field, source_key, checked_key in (
            ("representative_menu", "menu_source_url", "menu_checked_at"),
            ("detailed_category", "category_source_url", "category_checked_at")):
        value = str(metadata.get(field) or "")
        if (not re.search(r"브런치|조식|아침식사|\bbrunch\b|\bbreakfast\b", value, re.I)
                or re.search(r"없음|미제공|불가|제외|\b(?:no|not|without|tea)\b", value, re.I)):
            continue
        source, checked = metadata.get(source_key), metadata.get(checked_key)
        if not source or not checked or not any(
                isinstance(p, dict) and isinstance(p.get("fields"), list) and field in p["fields"]
                and p.get("url") == source and p.get("checked_at") == checked for p in provenance):
            continue
        try:
            return Fact[StrictBool](value=True, status="KNOWN", source=source, verified_at=checked)
        except ValidationError:
            continue
    return Fact[StrictBool]()


def breakfast_metadata(record):
    metadata = record.get("enriched_metadata") or {}
    if not isinstance(metadata, dict):
        return Fact[StrictBool](), {}
    try:
        offering = Fact[StrictBool].model_validate(_decoded(metadata.get("breakfast_offering")) or {})
    except (ValidationError, TypeError):
        offering = Fact[StrictBool]()
    if not offering.known:
        try:
            category = Fact[str].model_validate(_decoded(metadata.get("breakfast_category")) or {})
            if category.known and category.value in ("breakfast_restaurant", "breakfast_cafe", "brunch_cafe"):
                offering = Fact[StrictBool].model_validate({**category.model_dump(), "value": True})
        except (ValidationError, TypeError):
            pass
    # A supplied explicit negative or unknown fact must not be overridden by
    # older imported text. Missing structured facts can use pinned provenance.
    if not offering.known and "breakfast_offering" not in metadata and "breakfast_category" not in metadata:
        offering = _imported_offering(metadata)
    hours = {}
    raw_hours = _decoded(metadata.get("breakfast_hours")) or {}
    if isinstance(raw_hours, dict):
        for day, value in raw_hours.items():
            try:
                hours[date.fromisoformat(day)] = Fact[tuple[Window, ...]].model_validate(value)
            except (ValidationError, TypeError, ValueError):
                continue
    return offering, hours
