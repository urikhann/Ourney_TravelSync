"""Import successful saved Kakao edges locally; never makes provider requests."""
import json
from sqlalchemy import text
import main as adapter


def seed():
    imported, skipped = 0, 0
    with adapter.engine.connect() as connection:
        rows = connection.execute(text("SELECT planning_data FROM itinerary_optimization_runs ORDER BY id"))
        for (data,) in rows:
            if isinstance(data, str):
                data = json.loads(data)
            request, response = (data or {}).get("request", {}), (data or {}).get("response", {})
            # Audits before this adapter version or with unknown policies are not
            # transferable. Historical adapter default search radius was 5000.
            if response.get("routing_version") != adapter.ROUTING_VERSION or adapter.KAKAO_ROUTE_RADIUS != 5000:
                skipped += 1
                continue
            points = {p["poi_id"]: p for p in request.get("pois", [])}
            points.update(request.get("points", {}))
            entries = []
            for edge in response.get("edges", []):
                if edge.get("origin") in points and edge.get("destination") in points and adapter._valid_cached_route(edge):
                    entries.append((adapter._cache_key(points[edge["origin"]], points[edge["destination"]]), edge))
            adapter._save_routes(entries)
            imported += len(entries)
    print(json.dumps({"successful_saved_edges_processed": imported, "audits_skipped": skipped,
                      "provider_requests": 0, "expires": False}))


if __name__ == "__main__":
    seed()
