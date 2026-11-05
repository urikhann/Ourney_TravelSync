"""How a place is shown to the web app (shared by recommendations, plans and place details)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .features import FEATURES
from .metadata import clean_meta
from .models import Poi


def place_dict(p: Poi) -> dict:
    return {"id": p.id, "name": p.name, "area": p.area, "kind": p.kind, "category": p.category,
            "features": [f for f in FEATURES if (p.features or {}).get(f)],
            "indoor": p.indoor, "avg_cost_krw": p.avg_cost_krw, "duration_min": p.duration_min,
            "image_url": p.image_url or None, "place_url": p.place_url or None, "lat": p.lat, "lng": p.lng,
            "enriched_metadata": clean_meta(p.enriched_metadata),  # also cleans places imported before the fix
            "field_status": p.field_status or {}}


def load_places(db: Session, version: str, ids) -> dict[str, dict]:
    ids = list(set(ids))
    if not ids:
        return {}
    rows = db.scalars(select(Poi).where(Poi.dataset_version == version, Poi.id.in_(ids)))
    return {p.id: place_dict(p) for p in rows}
