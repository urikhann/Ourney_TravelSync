"""The agent's "brain": Gemini with function calling, plus a rules fallback.

One turn:
  1. Gemini reads the request and the recent chat, and calls tools (get_plan, find_places, check_hours)
  2. it finishes by calling `answer` with a short reply and, optionally, one suggested change
  3. our code checks the suggestion (real stop, real place, not closed) before the group ever sees it

If there is no GEMINI_API_KEY, or Gemini fails / runs out of quota, the rules fallback answers instead,
so chat remains available without the model service.
"""
import json
import logging
import os
import time

from tools import TripData

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
# Free-tier models are sometimes "experiencing high demand" (503) or rate-limited (429).
# Then the whole turn is tried again on the next model in this list.
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-2.5-flash").split(",") if m.strip()]
BUSY = {429, 500, 503, 504}
BUSY_PAUSE_SECONDS = 300            # a busy model is skipped for 5 minutes, so later questions don't wait for it again
busy_until: dict[str, float] = {}
log = logging.getLogger("uvicorn.error")
MAX_STEPS = 6
DEADLINE_SECONDS = 40  # the backend waits up to 60 s; stop early and use the rules instead

SYSTEM = """You are TravelSync, the assistant inside a group-travel planning app for Busan.
The group chats; you are only called when someone asks you.
Rules:
- Use the tools to look at the real plan and real places. Never invent places, times or prices.
- You may suggest at most ONE change (remove one stop and add one place at the same start time). The group approves it later.
- Only use poi_id and item_id values returned by the tools. Never change a locked stop.
- indoor_guess is inferred from the category; say "probably indoor", not "definitely".
- If opening hours are unknown (available = null), say they should check before going. Never call that "closed".
- If the request is unclear or impossible, ask one short question instead of guessing.
- Reply in the language of the LAST message (the "asks:" line), Korean or English. Keep it to 1-3 short sentences.
- The current plan is already given below the chat; call get_plan only if you need it again.
- For greetings or small talk, call `answer` right away without other tools.
- Always finish by calling the `answer` tool."""

TOOLS = [
    {"name": "get_plan", "description": "The group's current plan: days and stops with item_id, time, place facts.",
     "parameters_json_schema": {"type": "object", "properties": {}}},
    {"name": "find_places", "description": "Find places not yet in the plan. The group's own recommendations come first.",
     "parameters_json_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "description": "Same kind as the stop being replaced, e.g. 'activity', 'food', 'cafe'"},
         "indoor": {"type": "boolean", "description": "true = probably indoor places only"},
         "max_walking": {"type": "number", "description": "0 (little walking) to 1 (a lot)"},
         "max_cost_krw": {"type": "integer"},
         "limit": {"type": "integer", "description": "1-8, default 5"}}}},
    {"name": "check_hours", "description": "Is this place open for the whole visit? available: true, false or null (unknown).",
     "parameters_json_schema": {"type": "object", "required": ["poi_id", "day", "start"], "properties": {
         "poi_id": {"type": "string"}, "day": {"type": "string", "description": "YYYY-MM-DD"},
         "start": {"type": "string", "description": "HH:MM"}, "duration_min": {"type": "integer"}}}},
    {"name": "answer", "description": "Finish the turn: the reply for the group and, optionally, one suggested change.",
     "parameters_json_schema": {"type": "object", "required": ["reply"], "properties": {
         "reply": {"type": "string"},
         "needs_clarification": {"type": "boolean"},
         "proposal": {"type": "object", "properties": {
             "date": {"type": "string"},
             "remove_item_ids": {"type": "array", "items": {"type": "string"}},
             "add": {"type": "array", "items": {"type": "object", "properties": {
                 "poi_id": {"type": "string"}, "start": {"type": "string"}}}}}}}}},
]
SUMMARY = {"get_plan": "Read the plan", "find_places": "Searched places", "check_hours": "Checked opening hours"}


def run_tool(data: TripData, name: str, args: dict) -> dict:
    if name == "get_plan":
        return data.get_plan()
    if name == "find_places":
        return data.find_places(**{k: v for k, v in args.items() if k in ("kind", "indoor", "max_walking", "max_cost_krw", "limit")})
    if name == "check_hours":
        return data.check_hours(args.get("poi_id", ""), args.get("day", ""), args.get("start", ""), args.get("duration_min") or 90)
    return {"error": f"unknown tool {name}"}


def finish(data: TripData, reply: str, proposal: dict | None, calls: list, clarify: bool = False) -> dict:
    """Safety check, then the contract's response shape (contracts/agent.response.json)."""
    checked, problem = data.validate(proposal)
    if problem:
        reply += f" (I left out a suggested change because {problem}.)"
    return {"reply": reply.strip(), "tool_calls": calls, "proposal": checked, "needs_clarification": clarify}


def context_text(req: dict) -> str:
    """Recent chat with people shown as Member 1, 2… (no names or emails go to the model)."""
    who: dict = {}
    lines = []
    for m in req.get("recent_messages", []):
        label = "TravelSync" if m.get("kind") == "agent" else who.setdefault(m.get("user_id"), f"Member {len(who) + 1}")
        lines.append(f"{label}: {m.get('text', '')[:300]}")
    asker = who.setdefault(req["message"].get("user_id"), f"Member {len(who) + 1}")
    return ("Recent chat:\n" + "\n".join(lines[-8:]) + "\n\n" if lines else "") + f"{asker} asks: {req['message']['text']}"


# ---------------------------------------------------------------- Gemini
def gemini_turn(req: dict, data: TripData, client=None, model: str = GEMINI_MODEL, started: float | None = None,
                think: bool = True) -> dict:
    from google.genai import types

    if client is None:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                              http_options=types.HttpOptions(timeout=20_000,  # ms per Gemini call
                                                             retry_options=types.HttpRetryOptions(attempts=1)))  # we switch models instead
    # Little "thinking": these are short tool-picking steps, and thinking adds seconds to every call.
    thinking = (types.ThinkingConfig(thinking_budget=0) if model.startswith("gemini-2.5")
                else types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM, temperature=0.2, thinking_config=thinking if think else None,
        tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in TOOLS])],
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    plan = data.get_plan()  # given up front: saves one Gemini round trip on most questions
    prompt = context_text(req) + "\n\nCurrent plan (JSON):\n" + json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
    calls = []
    started = started or time.monotonic()
    for _ in range(MAX_STEPS):
        if time.monotonic() - started > DEADLINE_SECONDS:
            raise TimeoutError("Gemini took too long")
        resp = client.models.generate_content(model=model, contents=contents, config=config)
        fcs = resp.function_calls or []
        if not fcs:  # plain text instead of a tool call
            return finish(data, resp.text or "Sorry, I couldn't work that out.", None, calls)
        contents.append(resp.candidates[0].content)
        parts = []
        for fc in fcs:
            args = dict(fc.args or {})
            if fc.name == "answer":
                return finish(data, args.get("reply", ""), args.get("proposal"), calls, bool(args.get("needs_clarification")))
            out = run_tool(data, fc.name, args)
            calls.append({"name": fc.name, "summary": SUMMARY.get(fc.name, fc.name)})
            parts.append(types.Part.from_function_response(name=fc.name, response={"result": out}))
        contents.append(types.Content(role="user", parts=parts))
    return finish(data, "Sorry, that took too many steps. Could you ask in a simpler way?", None, calls, True)


# ---------------------------------------------------------------- rules fallback (no LLM)
WANTS = {"indoor": ["rain", "indoor", "비", "실내"], "low walking": ["tired", "walk", "피곤", "걷"],
         "cheaper": ["cheap", "budget", "저렴", "싸"]}


def rules_turn(req: dict, data: TripData) -> dict:
    t = req["message"]["text"].lower()
    want = next((w for w, keys in WANTS.items() if any(k in t for k in keys)), None)
    calls = [{"name": "rules", "summary": "Simple rules (AI not connected)"}]
    if want is None:
        return finish(data, "I can help with rain (indoor places), less walking or a cheaper plan.", None, calls, True)
    plan = data.get_plan()["plan"]
    if not plan:
        return finish(data, "There is no plan yet — build plans on the Plan tab first, then ask me again.", None, calls)
    for item in plan[0]["items"]:
        if item.get("locked") or item.get("kind") == "food":
            continue
        if want == "indoor" and item.get("indoor_guess"):
            continue
        args = {"kind": item.get("kind"), "limit": 3}
        if want == "indoor":
            args["indoor"] = True
        elif want == "low walking":
            args["max_walking"] = max(0.0, (item.get("walking_level") or 0.5) - 0.1)
        else:
            args["max_cost_krw"] = max(0, (item.get("avg_cost_krw") or 0) - 1)
        found = data.find_places(**args)["places"]
        calls.append({"name": "find_places", "summary": "Searched places"})
        for cand in found:
            hours = data.check_hours(cand["poi_id"], plan[0]["date"], item["start"], cand.get("duration_min") or 90)
            if hours["available"] is not False:
                calls.append({"name": "check_hours", "summary": "Checked opening hours"})
                note = "" if hours["available"] else " Its opening hours aren't known, so check before going."
                return finish(data, f"How about swapping {item['name']} ({item['start']}) for {cand['name']}?{note}",
                              {"date": plan[0]["date"], "remove_item_ids": [item["item_id"]],
                               "add": [{"poi_id": cand["poi_id"], "start": item["start"]}]}, calls)
    return finish(data, "I couldn't find a good swap in today's plan.", None, calls)


def gemini_with_fallback(req: dict, data: TripData, client=None) -> dict:
    """Try GEMINI_MODEL, then the fallback models when Google says the model is busy or rate-limited."""
    from google.genai import errors

    started, last = time.monotonic(), None
    models = list(dict.fromkeys([GEMINI_MODEL, *FALLBACK_MODELS]))
    ready = [m for m in models if busy_until.get(m, 0) < started] or models  # all paused -> try them anyway
    for model in ready:
        try:
            try:
                out = gemini_turn(req, data, client, model=model, started=started)
            except errors.ClientError as e:  # some models reject the thinking setting -> once more without it
                if getattr(e, "code", None) != 400 or "think" not in str(e).lower():
                    raise
                out = gemini_turn(req, data, client, model=model, started=started, think=False)
            log.info("agent turn: %s answered in %.1f s", model, time.monotonic() - started)
            if model != GEMINI_MODEL:
                out["tool_calls"].insert(0, {"name": "model", "summary": f"Used {model} ({GEMINI_MODEL} was busy)"})
            return out
        except errors.APIError as e:
            if getattr(e, "code", None) not in BUSY:
                raise
            busy_until[model] = time.monotonic() + BUSY_PAUSE_SECONDS
            log.info("agent turn: %s busy (%s), trying the next model", model, e.code)
            last = e
    raise last or RuntimeError("no Gemini model available")


def turn(req: dict, client=None) -> dict:
    data = TripData(req.get("itinerary_id"))
    if client is not None or os.getenv("GEMINI_API_KEY"):
        try:
            return gemini_with_fallback(req, data, client)
        except Exception as e:  # Use rules when the model request fails.
            out = rules_turn(req, data)
            out["tool_calls"].insert(0, {"name": "gemini_error", "summary": f"AI unavailable ({type(e).__name__})"})
            return out
    return rules_turn(req, data)
