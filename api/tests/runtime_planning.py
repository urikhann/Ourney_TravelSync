"""Explicit synthetic upstream for the opt-in loopback test; never production config."""
import os
from pathlib import Path
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"synthetic": True}


@app.post("/snapshot")
def snapshot(req: dict):
    mode = Path(os.environ["TRAVELSYNC_SYNTHETIC_MODE_FILE"]).read_text()
    def fact(value):
        return dict(value=value, status="KNOWN", source="synthetic loopback fixture", verified_at="2026-11-01T00:00:00Z")
    ids = [p["poi_id"] for p in req["pois"]]
    return dict(schema_version="planning-data-1.0", context_id=req["context_id"], dataset_version=req["dataset_version"],
                transport_mode=req["transport_mode"], operating_snapshot_id="synthetic-live-ops", routing_version="synthetic-live-routes",
                pois=[dict(poi_id=pid, duration_min=fact(30), cost_per_person={} if mode == "unknown" else fact(100),
                           operations={d["date"]: dict(openings=fact([dict(start_min=480, end_min=1320)]),
                                                      closed=fact(False), event_active=fact(True)) for d in req["days"]}) for pid in ids],
                edges=[dict(date=d["date"], origin=a, destination=b, status="KNOWN", duration_minutes=10,
                            group_fare=10, walk_minutes=2, distance_m=500,
                            provider="synthetic loopback fixture", fetched_at="2026-11-01T00:00:00Z")
                       for d in req["days"] for a in [d["start_point"], *ids] for b in [*ids, d["end_point"]] if a != b])
