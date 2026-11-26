"""Read-only tools the agent may use. They read the app's database (like services/mocks does) and the
availability checker. None of them change anything: a suggested change is only a proposal the group approves.

Every tool returns plain JSON-friendly dicts, so the same functions serve Gemini and the rules fallback.
"""
import json
import os
from datetime import date

import httpx
from sqlalchemy import bindparam, create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///../../api/travelsync.db")
AVAILABILITY_URL = os.getenv("AVAILABILITY_URL", "http://localhost:8001")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})


def _json(v):
    return v if isinstance(v, (dict, list)) or v is None else json.loads(v)


def _place(r) -> dict:
    """What the agent may know about a place. `indoor` comes from the place's categories (rule-based guess)."""
    return {"poi_id": r["id"], "name": r["name"], "area": r["area"], "kind": r["kind"], "category": r["category"],
            "indoor_guess": bool(r["indoor"]), "walking_level": r["walking_level"],
            "duration_min": r["duration_min"]}


PLACE_COLS = "id, name, area, kind, category, indoor, walking_level, duration_min"


class TripData:
    """Everything one agent turn may look at, loaded once from the itinerary the backend sent."""

    def __init__(self, itinerary_id: int | None):
        self.itinerary_id = itinerary_id
        self.days, self.version, self.group, self.places = [], None, [], {}
        self.recommendation_reasons = {}
        if not itinerary_id:
            return
        with engine.connect() as c:
            row = c.execute(text("SELECT i.days, r.dataset_version, r.response FROM itineraries i "
                                 "JOIN recommendation_runs r ON r.id = i.run_id WHERE i.id = :i"),
                            {"i": itinerary_id}).mappings().first()
            if row is None:
                return
            self.days = _json(row["days"]) or []
            self.version = row["dataset_version"]
            recommendations = (_json(row["response"]) or {}).get("group", [])
            self.group = [g["poi_id"] for g in recommendations]
            ids = {it["poi_id"] for d in self.days for it in d["items"]}
            for entry in recommendations:
                if entry["poi_id"] in ids:
                    reason = next((r["text"] for r in entry.get("reasons", [])
                                   if r.get("code") == "shared_interest" and not r.get("user_id")
                                   and isinstance(r.get("text"), str) and r["text"].strip()), None)
                    if reason:
                        self.recommendation_reasons[entry["poi_id"]] = reason
            ids.update(self.group)
            if ids:
                q = text(f"SELECT {PLACE_COLS} FROM pois WHERE dataset_version = :v AND id IN :ids")
                q = q.bindparams(bindparam("ids", expanding=True))
                for r in c.execute(q, {"v": self.version, "ids": list(ids)}).mappings():
                    self.places[r["id"]] = _place(r)

    # ---------------------------------------------------------------- tools
    def get_plan(self) -> dict:
        """The group's current plan (plan A), day by day."""
        if not self.days:
            return {"plan": None, "note": "This trip has no plan yet. Ask the group to build plans on the Plan tab."}
        return {"plan": [{"date": d["date"], "items": [
            {"item_id": f"{d['date']}@{it['start']}", "start": it["start"], "end": it["end"],
             **getattr(self, "protection", {}).get(f"{d['date']}@{it['start']}", {"locked": it.get("locked", False)}),
             "group_recommendation_reason": self.recommendation_reasons.get(it["poi_id"]),
             "available_when_planned": (it.get("availability") or {}).get("available"),
             **self.places.get(it["poi_id"], {"poi_id": it["poi_id"], "name": it["poi_id"]})} for it in d["items"]]}
            for d in self.days]}

    def find_places(self, kind: str | None = None, indoor: bool | None = None, max_walking: float | None = None,
                    limit: int = 5) -> dict:
        """Places that are not in the plan yet. The group's own recommendations come first (they fit the group)."""
        used = {it["poi_id"] for d in self.days for it in d["items"]}
        limit = max(1, min(int(limit or 5), 8))

        def ok(p):
            return (p["poi_id"] not in used and (kind is None or p["kind"] == kind)
                    and (indoor is None or p["indoor_guess"] == indoor)
                    and (max_walking is None or (p["walking_level"] if p["walking_level"] is not None else 1) <= max_walking))

        picks = [self.places[i] for i in self.group if i in self.places and ok(self.places[i])][:limit]
        source = "group recommendations"
        if len(picks) < limit and self.version:  # top up from the whole catalogue
            where = ["dataset_version = :v"]
            args: dict = {"v": self.version}
            if kind:
                where.append("kind = :k"); args["k"] = kind
            if indoor is not None:
                where.append("indoor = :in"); args["in"] = indoor
            with engine.connect() as c:
                rows = c.execute(text(f"SELECT {PLACE_COLS} FROM pois WHERE {' AND '.join(where)} ORDER BY id LIMIT 400"),
                                 args).mappings().all()
            for r in rows:
                p = _place(r)
                if ok(p) and p["poi_id"] not in {x["poi_id"] for x in picks}:
                    picks.append(p)
                    self.places.setdefault(p["poi_id"], p)
                if len(picks) >= limit:
                    break
            source = "group recommendations + other places"
        return {"places": picks, "source": source,
                "note": "indoor_guess is inferred from the place category, not verified."}

    def check_hours(self, poi_id: str, day: str, start: str, duration_min: int = 90) -> dict:
        """Ask the availability checker. available: true / false / null (= hours unknown, not closed)."""
        try:
            date.fromisoformat(day)
            r = httpx.post(f"{AVAILABILITY_URL}/availability/check", timeout=8,
                           json={"checks": [{"poi_id": poi_id, "date": day, "start": start, "duration_min": int(duration_min), "dataset_version": self.version}]})
            r.raise_for_status()
            res = r.json()["results"][0]
            return {"poi_id": poi_id, "available": res["available"], "reason": res.get("reason"),
                    "open": res.get("open"), "close": res.get("close")}
        except Exception:
            return {"poi_id": poi_id, "available": None, "reason": "Opening hours could not be checked"}

    # ---------------------------------------------------------------- safety check for proposals
    def validate(self, proposal: dict | None) -> tuple[dict | None, str | None]:
        """Only real items and real places get through. Returns (proposal or None, problem or None)."""
        if not proposal:
            return None, None
        items = {f"{d['date']}@{it['start']}": (d["date"], it) for d in self.days for it in d["items"]}
        used = {it["poi_id"] for d in self.days for it in d["items"]}
        remove = [i for i in proposal.get("remove_item_ids") or [] if i in items]
        if len(remove) != len(proposal.get("remove_item_ids") or []):
            return None, "it referred to a stop that is not in the plan"
        if any(getattr(self, "protection", {}).get(i, {}).get("locked", items[i][1].get("locked")) or
               getattr(self, "protection", {}).get(i, {}).get("completed", False) for i in remove):
            return None, "it tried to change a locked stop"
        day = proposal.get("date") or (items[remove[0]][0] if remove else None)
        if day is None or (remove and any(items[i][0] != day for i in remove)):
            return None, "it mixed up the days"
        add = []
        for a in proposal.get("add") or []:
            pid, start = a.get("poi_id"), a.get("start")
            if pid in used or pid not in self.places or not start:
                return None, "it suggested a place that is unknown or already in the plan"
            hours = self.check_hours(pid, day, start, self.places[pid].get("duration_min") or 90)
            if hours["available"] is False:
                return None, f"{self.places[pid]['name']} is not open then ({hours['reason']})"
            add.append({"poi_id": pid, "start": start})
        if not remove and not add:
            return None, None
        return {"date": day, "remove_item_ids": remove, "add": add}, None
