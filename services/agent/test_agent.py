"""Agent service tests. No real Gemini call: a fake client replays tool calls the way Gemini sends them.

    cd services/agent && python -m pytest -q
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

DB = Path(tempfile.gettempdir()) / "travelsync_agent_test.db"
DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ.pop("GEMINI_API_KEY", None)
sys.path.insert(0, str(Path(__file__).parent))

con = sqlite3.connect(DB)
con.executescript("""
CREATE TABLE pois (id TEXT, dataset_version TEXT, name TEXT, area TEXT, kind TEXT, category TEXT, indoor BOOLEAN,
                   walking_level FLOAT, avg_cost_krw INTEGER, duration_min INTEGER);
CREATE TABLE recommendation_runs (id INTEGER PRIMARY KEY, dataset_version TEXT, response TEXT);
CREATE TABLE itineraries (id INTEGER PRIMARY KEY, run_id INTEGER, days TEXT);
""")
P = [("park", "Busan Park", 0, 0.8, 0, 90), ("museum", "Busan Museum", 1, 0.2, 0, 90), ("gallery", "Small Gallery", 1, 0.3, 3000, 60),
     ("lunch", "Pork Soup", 1, 0.1, 9000, 60), ("market", "Fish Market", 0, 0.9, 0, 60)]
for pid, name, indoor, walk, cost, dur in P:
    kind = "food" if pid == "lunch" else "activity"
    con.execute("INSERT INTO pois VALUES (?, 'v1', ?, 'Jung-gu', ?, 'test', ?, ?, ?, ?)", (pid, name, kind, indoor, walk, cost, dur))
con.execute("INSERT INTO recommendation_runs VALUES (1, 'v1', ?)",
            (json.dumps({"group": [{"poi_id": i} for i in ["park", "lunch", "museum", "market", "gallery"]]}),))
days = [{"date": "2026-11-21", "items": [
    {"poi_id": "park", "start": "10:00", "end": "11:30", "locked": False},
    {"poi_id": "lunch", "start": "12:30", "end": "13:30", "locked": False},
    {"poi_id": "market", "start": "14:00", "end": "15:00", "locked": True}]}]
con.execute("INSERT INTO itineraries VALUES (7, 1, ?)", (json.dumps(days),))
con.commit()
con.close()

import brain  # noqa: E402
import tools  # noqa: E402
from google.genai import types  # noqa: E402

HOURS = {}
tools.TripData.check_hours = lambda self, poi_id, day, start, duration_min=90: {
    "poi_id": poi_id, "available": HOURS.get(poi_id, True), "reason": "Closed on Saturdays" if HOURS.get(poi_id) is False else None}


def req(text, itinerary_id=7):
    return {"trip_id": "t1", "itinerary_id": itinerary_id,
            "message": {"id": 9, "user_id": "u1", "text": text, "sent_at": "2026-11-20T10:00:00+09:00"},
            "recent_messages": [{"id": 8, "user_id": "u2", "kind": "user", "text": "rain tomorrow!"}]}


class FakeGemini:
    """Plays back a list of tool calls, one model response per step, like the real API."""

    def __init__(self, steps):
        self.steps, self.seen = list(steps), []
        self.models = self

    def generate_content(self, model, contents, config):
        self.seen.append(contents[-1])
        name, args = self.steps.pop(0)
        part = types.Part(function_call=types.FunctionCall(name=name, args=args))
        return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))])


def test_gemini_tool_loop_produces_a_checked_proposal():
    HOURS.clear()
    fake = FakeGemini([
        ("get_plan", {}),
        ("find_places", {"kind": "activity", "indoor": True}),
        ("answer", {"reply": "Rain tomorrow — swap Busan Park for Busan Museum?",
                    "proposal": {"date": "2026-11-21", "remove_item_ids": ["2026-11-21@10:00"],
                                 "add": [{"poi_id": "museum", "start": "10:00"}]}}),
    ])
    out = brain.turn(req("@TravelSync it's raining tomorrow, indoor ideas?"), client=fake)
    assert out["proposal"] == {"date": "2026-11-21", "remove_item_ids": ["2026-11-21@10:00"], "add": [{"poi_id": "museum", "start": "10:00"}]}
    assert [c["name"] for c in out["tool_calls"]] == ["get_plan", "find_places"]
    # the model got real tool results back, and saw members as "Member n", never ids or names
    first = fake.seen[0].parts[0].text
    assert "Member 1: rain tomorrow!" in first and "u1" not in first and "u2" not in first
    assert "Busan Park" in first  # the plan is given up front, so most answers need fewer Gemini calls
    plan = fake.seen[1].parts[0].function_response.response["result"]["plan"]
    assert plan[0]["items"][0]["name"] == "Busan Park"


def test_bad_proposals_are_dropped_not_shown():
    HOURS.clear()
    cases = [
        ({"date": "2026-11-21", "remove_item_ids": ["2026-11-21@09:00"], "add": [{"poi_id": "museum", "start": "09:00"}]}, "not in the plan"),
        ({"date": "2026-11-21", "remove_item_ids": ["2026-11-21@14:00"], "add": [{"poi_id": "museum", "start": "14:00"}]}, "locked"),
        ({"date": "2026-11-21", "remove_item_ids": ["2026-11-21@10:00"], "add": [{"poi_id": "eiffel", "start": "10:00"}]}, "unknown"),
    ]
    for proposal, why in cases:
        fake = FakeGemini([("find_places", {}), ("answer", {"reply": "Try this.", "proposal": proposal})])
        out = brain.turn(req("@TravelSync swap something"), client=fake)
        assert out["proposal"] is None and why in out["reply"], (why, out["reply"])
    HOURS["museum"] = False
    fake = FakeGemini([("find_places", {"indoor": True}), ("answer", {"reply": "Museum?", "proposal": cases[0][0] | {
        "remove_item_ids": ["2026-11-21@10:00"], "add": [{"poi_id": "museum", "start": "10:00"}]}})])
    out = brain.turn(req("@TravelSync indoor"), client=fake)
    assert out["proposal"] is None and "not open" in out["reply"]


def test_gemini_failure_falls_back_to_rules():
    HOURS.clear()

    class Broken:
        models = None

        def __init__(self):
            self.models = self

        def generate_content(self, **kw):
            raise RuntimeError("quota")
    out = brain.turn(req("@TravelSync it's raining"), client=Broken())
    assert out["tool_calls"][0]["name"] == "gemini_error"
    assert out["proposal"]["add"][0]["poi_id"] == "museum"  # indoor, same kind, from the group's list first


def test_rules_without_key_and_without_plan():
    HOURS.clear()
    out = brain.turn(req("@TravelSync we're tired, less walking please"))
    assert out["proposal"]["remove_item_ids"] == ["2026-11-21@10:00"] and out["proposal"]["add"][0]["poi_id"] == "museum"
    out = brain.turn(req("@TravelSync 비가 와요"))  # Korean keywords work too
    assert out["proposal"] is not None
    out = brain.turn(req("@TravelSync it's raining", itinerary_id=None))
    assert out["proposal"] is None and "Plan tab" in out["reply"]
    out = brain.turn(req("@TravelSync hello"))
    assert out["needs_clarification"] is True


def test_http_contract():
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    assert c.get("/health").json()["brain"] == "rules"
    r = c.post("/agent/turn", json=req("@TravelSync rain"))
    assert r.status_code == 200 and set(r.json()) >= {"reply", "tool_calls", "proposal"}
    assert c.post("/agent/turn", json={"message": {"text": " "}}).status_code == 422


def test_busy_model_switches_to_the_next_one():
    from google.genai import errors
    HOURS.clear()

    class BusyThenOk(FakeGemini):
        def generate_content(self, model, contents, config):
            self.models_used = getattr(self, "models_used", []) + [model]
            if model == brain.GEMINI_MODEL:
                raise errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
            return super().generate_content(model, contents, config)
    fake = BusyThenOk([("answer", {"reply": "Hi! Ask me about rain or walking."})])
    brain.busy_until.clear()
    out = brain.turn(req("@TravelSync hello"), client=fake)
    assert out["reply"].startswith("Hi!") and fake.models_used == [brain.GEMINI_MODEL, brain.FALLBACK_MODELS[0]]
    assert "busy" in out["tool_calls"][0]["summary"]
    # the next question skips the busy model straight away (no waiting for it again)
    fake.steps = [("answer", {"reply": "Hi again!"})]
    fake.models_used = []
    out = brain.turn(req("@TravelSync hello again"), client=fake)
    assert fake.models_used == [brain.FALLBACK_MODELS[0]] and out["reply"] == "Hi again!"
    brain.busy_until.clear()


def test_chat_tools_do_not_expose_cost_filters_or_prices():
    data = tools.TripData(7)
    assert "avg_cost_krw" not in data.get_plan()["plan"][0]["items"][0]
    assert all("avg_cost_krw" not in p for p in data.find_places()["places"])
    declaration = next(t for t in brain.TOOLS if t["name"] == "find_places")
    assert "max_cost_krw" not in declaration["parameters_json_schema"]["properties"]


def test_budget_request_does_not_propose_cost_optimization():
    out = brain.rules_turn(req("@TravelSync make it cheaper, lower budget please"), tools.TripData(7))
    assert out["proposal"] is None and out["needs_clarification"]
    assert "cheaper" not in out["reply"] and "budget" not in out["reply"]


def test_provider_error_reports_status_without_leaking_response(monkeypatch):
    from google.genai import errors
    secret = "private-key-and-prompt-must-not-appear"
    def fail(*args, **kwargs):
        raise errors.ClientError(404, {"error":{"code":404,"status":"NOT_FOUND","message":secret}})
    monkeypatch.setattr(brain, "gemini_with_fallback", fail)
    out = brain.turn(req("@TravelSync hello"), client=object())
    summary = out["tool_calls"][0]["summary"]
    assert "HTTP 404" in summary and "model unavailable" in summary
    assert secret not in json.dumps(out)
    rate_limit = errors.ClientError(429,{"error":{"code":429,"message":secret}})
    assert "quota or rate limit" in brain.provider_failure_summary(rate_limit)


def test_hours_tool_sends_pinned_dataset(monkeypatch):
    # Use a fresh class method: the module's legacy fixtures replace check_hours globally.
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_pinned_tools_test", Path(__file__).parent / "tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seen = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"results":[{"available":None}]}
    def post(*args, **kwargs):
        seen.append(kwargs["json"])
        return Response()
    monkeypatch.setattr(module.httpx,"post",post)
    data = module.TripData(None)
    data.version = "historical-v1"
    assert data.check_hours("museum","2026-11-21","10:00")["available"] is None
    assert seen[0]["checks"][0]["dataset_version"] == "historical-v1"
    module.engine.dispose()
