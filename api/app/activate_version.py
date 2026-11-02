"""Switch which imported dataset version the app uses (exactly one is ACTIVE).

    cd api
    python -m app.activate_version                      # list versions
    python -m app.activate_version busan-enriched-v1    # use all 3,570 places again
    python -m app.activate_version busan-demo40-v1      # use the 40 demo places

Remember: the recommender service must be started with the same DATASET_VERSION (and PLACES_CSV for the demo).
"""
import sys
from datetime import datetime, timezone

from sqlalchemy import select, update

from .db import SessionLocal
from .models import DatasetVersion


def activate(version: str) -> None:
    with SessionLocal() as s:
        if not s.get(DatasetVersion, version):
            raise SystemExit(f"No dataset version '{version}'. Import it first with app.import_places.")
        s.execute(update(DatasetVersion).where(DatasetVersion.status == "ACTIVE").values(status="ARCHIVED"))
        dv = s.get(DatasetVersion, version)
        dv.status, dv.activated_at = "ACTIVE", datetime.now(timezone.utc)
        s.commit()
    print(f"ACTIVE: {version}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        activate(sys.argv[1])
    with SessionLocal() as s:
        for v in s.scalars(select(DatasetVersion).order_by(DatasetVersion.version)):
            print(f"  {v.status:8} {v.version}  ({v.poi_count} places)")
