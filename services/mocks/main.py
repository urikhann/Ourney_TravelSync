"""Mock versions of the teammates' services. Same JSON as contracts/. Replace one by one."""
import os
from datetime import date, datetime, timedelta

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text

import group_rec
from group_rec import FEATURES

FEATURE_SCHEMA_VERSION = "fs-1.0"

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///../../api/travelsync.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
app = FastAPI(title="TravelSync mock services", version="0.1.0")

LOVE = {"food": "love local food", "cafe": "love cafés", "nature": "love nature & views", "history": "love history",
        "art": "love art & shows", "shopping": "love shopping", "activity": "love activities",
        "entertainment": "love fun & entertainment", "relax": "love rest & walks", "festival": "love festivals"}
BUFFER_MIN = 15


def _json(v):
    import json
    return v if isinstance(v, (dict, list)) else json.loads(v or "null")


def load_pois(version: str) -> dict[str, dict]:
    """All places of one dataset version (the version the backend pinned for this run)."""
    sql = ("SELECT id, name, kind, indoor, avg_cost_krw, duration_min, tags, features, walking_level, "
           "closed_on_public_holidays FROM pois WHERE dataset_version = :v")
    with engine.connect() as c:
        rows = c.execute(text(sql), {"v": version}).mappings().all()
    return {r["id"]: {**r, "tags": _json(r["tags"]) or [], "features": _json(r["features"]) or {}} for r in rows}


# ------------------------------------------------------------------ recommender (SDD 8–9: weighted sum + GFAR)
def reasons(poi: dict, members: list[dict], picked_for: str | None) -> list[dict]:
    f, out = poi["features"], []
    active = [c for c in FEATURES if f.get(c)]
    if active:
        best = max(active, key=lambda c: (sum(m["ratings"][c] for m in members), -FEATURES.index(c)))
        n = sum(1 for m in members if m["ratings"][best] >= 4)
        out.append({"code": "shared_interest",
                    "text": f"{n} of {len(members)} {LOVE[best]}" if n >= 2 else f"Matches {best}"})
    if picked_for and len(members) > 1:
        out.append({"code": "fair_pick", "text": f"Fair pick for {picked_for}", "user_id": picked_for})
    if poi.get("avg_cost_krw") == 0:
        out.append({"code": "free", "text": "Free"})
    if poi.get("indoor"):
        out.append({"code": "indoor", "text": "Indoor"})
    return out[:3]


@app.post("/recommend")
def recommend(req: dict):
    if req.get("feature_schema_version") not in (None, FEATURE_SCHEMA_VERSION):
        raise HTTPException(422, "feature_schema_version_mismatch")
    members, opts = req["members"], req.get("options", {})
    places = load_pois(req["dataset_version"])
    res = group_rec.recommend(members, places, top_l=opts.get("top_l", 100), group_k=opts.get("group_k", 30),
                              method=opts.get("method", "gfar"), awm_threshold=opts.get("awm_threshold"))
    for g in res["group"]:
        g["reasons"] = reasons(places[g["poi_id"]], members, g["picked_for"])
    return {"schema_version": req.get("schema_version", "rec-1.0"), "model_version": "mock-gfar-0.2",
            "dataset_version": req["dataset_version"], "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "method": opts.get("method", "gfar"), **res}


# ------------------------------------------------------------------ availability checker
def _min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hhmm(m: int) -> str:
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def check_one(poi_id: str, day: date, start: str, duration: int) -> dict:
    dow = (day.weekday() + 1) % 7  # Python Monday=0 -> our Sunday=0
    with engine.connect() as c:
        poi = c.execute(text("SELECT closed_on_public_holidays FROM pois WHERE id=:i LIMIT 1"), {"i": poi_id}).first()
        hours = c.execute(text("SELECT open, close FROM poi_hours WHERE poi_id=:i AND dow=:d"), {"i": poi_id, "d": dow}).first()
        holiday = c.execute(text("SELECT name FROM public_holidays WHERE day=:d"), {"d": day}).first()
        exc = c.execute(text("SELECT closed, open, close, reason FROM poi_exceptions WHERE poi_id=:i AND day=:d"),
                        {"i": poi_id, "d": day}).first()
    base = {"poi_id": poi_id, "confidence": 0.7, "source": "sample", "last_updated": "2026-09-28"}
    if poi is None:
        return {**base, "available": False, "reason": "Unknown place", "open": None, "close": None, "next_available": None}

    def next_open():
        for k in range(1, 8):
            d2 = day + timedelta(days=k)
            if check_one(poi_id, d2, "00:00", 0)["available"]:
                return d2.isoformat()
        return None

    if exc and exc.closed:
        return {**base, "available": False, "reason": f"Closed: {exc.reason or 'special closure'}", "open": None,
                "close": None, "next_available": next_open()}
    if holiday and poi.closed_on_public_holidays:
        return {**base, "available": False, "reason": f"Closed on public holidays ({holiday.name})", "open": None,
                "close": None, "next_available": next_open()}
    if not hours:
        return {**base, "available": False, "reason": f"Closed on {day.strftime('%A')}s", "open": None, "close": None,
                "next_available": next_open()}
    o, cl = (exc.open, exc.close) if exc and exc.open else (hours.open, hours.close)
    om, cm = _min(o), _min(cl)
    if cm <= om:
        cm += 24 * 60  # closes after midnight
    s = _min(start)
    if duration and s < om:
        return {**base, "available": False, "reason": f"Opens at {o}", "open": o, "close": cl, "next_available": None}
    end = s + duration + BUFFER_MIN
    if duration and end > cm:
        return {**base, "available": False, "reason": f"Visit ends {_hhmm(end)}, after closing {cl}", "open": o,
                "close": cl, "next_available": None}
    return {**base, "available": True, "reason": None, "open": o, "close": cl, "next_available": None}


@app.post("/availability/check")
def availability(req: dict):
    return {"results": [check_one(c["poi_id"], date.fromisoformat(c["date"]), c["start"], c["duration_min"])
                        for c in req["checks"]]}


# ------------------------------------------------------------------ itinerary optimizer (greedy)
SLOTS = [("10:00", "activity"), ("12:30", "food"), ("14:00", "activity"), ("16:00", "activity"), ("18:30", "food")]
BIAS = {"best": {}, "culture": {"history": 0.9, "art": 0.9, "nature": 0.5}, "food": {"food": 0.9, "cafe": 0.9}}


@app.post("/plans")
def plans(req: dict):
    pois = load_pois(req["dataset_version"])
    ranked = [r for r in req["candidates"] if r["poi_id"] in pois]
    locked = {(l["date"], l["start"]): l["poi_id"] for l in req.get("locked_items", [])}
    check = req.get("check_availability", True)  # False in the first version: no opening-hours checks
    out = []
    for variant in req.get("variants", ["best"]):
        bias = BIAS.get(variant, {})

        def score(r):
            f = pois[r["poi_id"]]["features"]
            return r["group_utility"] / 1000 + sum(bias.get(c, 0) * w for c, w in f.items()) / (sum(f.values()) or 1)

        order = sorted(ranked, key=score, reverse=True)
        used, days = set(), []
        for d in req["dates"]:
            items = []
            for i, (t, kind) in enumerate(SLOTS):
                nxt = _min(SLOTS[i + 1][0]) if i + 1 < len(SLOTS) else _min("21:00")
                if (d, t) in locked:
                    pid = locked[(d, t)]
                    used.add(pid)
                    items.append({"poi_id": pid, "start": t, "end": _hhmm(_min(t) + pois[pid]["duration_min"]),
                                  "locked": True, "moved_from": None, "availability": None})
                    continue
                for r in order:
                    p = pois[r["poi_id"]]
                    if r["poi_id"] in used or p["kind"] != kind or _min(t) + p["duration_min"] > nxt - 10:
                        continue
                    av = check_one(r["poi_id"], date.fromisoformat(d), t, p["duration_min"]) if check else None
                    if av is None or av["available"]:
                        used.add(r["poi_id"])
                        items.append({"poi_id": r["poi_id"], "start": t, "end": _hhmm(_min(t) + p["duration_min"]),
                                      "locked": False, "moved_from": None,
                                      "availability": {"available": True, "close": av["close"]} if av else None})
                        break
            days.append({"date": d, "items": items})
        all_items = [it for dd in days for it in dd["items"]]
        fit = {r["poi_id"]: r.get("fit", r["mean_score"]) for r in ranked}  # 0–1 fit (backend converts 1–5)
        costs = [pois[i["poi_id"]]["avg_cost_krw"] for i in all_items]
        out.append({"variant": variant, "days": days, "stats": {
            "group_fit": round(sum(fit.get(i["poi_id"], 0) for i in all_items) / (len(all_items) or 1), 4),
            "cost_per_person": sum(c for c in costs if c is not None),
            "cost_unknown_count": sum(1 for c in costs if c is None),  # unknown is never counted as free
            "walking_km_per_day": None}})
    return {"plans": out}


# ------------------------------------------------------------------ AI agent (keyword stub, no LLM)
@app.post("/agent/turn")
def agent_turn(req: dict):
    t = req["message"]["text"].lower()
    wants = [w for w, keys in {"indoor": ["rain", "indoor"], "low walking": ["tired", "walk"],
                               "cheaper": ["cheap", "budget"]}.items() if any(k in t for k in keys)]
    if not wants:
        return {"reply": "I can re-plan for rain, less walking or a lower budget. (mock agent)", "tool_calls": [], "proposal": None}
    return {"reply": f"(mock agent) I would look for places that are {', '.join(wants)}. The real agent will propose changes.",
            "tool_calls": [{"name": "understand_request", "summary": ", ".join(wants)}], "proposal": None}


@app.get("/health")
def health():
    return {"ok": True, "mock": True}
