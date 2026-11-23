"""The agent's "brain": replaceable Gemini/Groq adapters and a rules fallback.

One turn:
  1. The selected model reads recent chat and calls tools (get_plan, find_places, check_hours)
  2. it finishes by calling `answer` with a short reply and, optionally, one suggested change
  3. our code checks the suggestion (real stop, real place, not closed) before the group ever sees it

If the selected provider has no key or fails, the rules fallback answers instead,
so chat remains available without the model service.
"""
import json
from copy import deepcopy
import logging
import os
import time

from jsonschema import Draft202012Validator

from providers import GeminiProvider, GroqProvider, GROQ_MODELS, ModelProvider, ProviderError
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
- Use the tools to look at the real plan and real places. Never invent places or times.
- Budget and price optimization are outside product scope. Do not offer cheaper plans or cost comparisons.
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


def strict_tools() -> list[dict]:
    """Close argument objects and require a complete single-swap proposal."""
    definitions = deepcopy(TOOLS)

    def close_objects(schema):
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                schema["additionalProperties"] = False
            for value in list(schema.values()):
                close_objects(value)
        elif isinstance(schema, list):
            for value in schema:
                close_objects(value)

    close_objects(definitions)
    proposal = definitions[-1]["parameters_json_schema"]["properties"]["proposal"]
    proposal["type"] = ["object", "null"]
    proposal["description"] = "Omit or use null when no change is suggested; otherwise provide one complete swap."
    proposal["required"] = ["date", "remove_item_ids", "add"]
    for field in ("remove_item_ids", "add"):
        proposal["properties"][field].update(minItems=1, maxItems=1, uniqueItems=True)
    proposal["properties"]["add"]["items"]["required"] = ["poi_id", "start"]
    return definitions


def run_tool(data: TripData, name: str, args: dict) -> dict:
    if name == "get_plan":
        return data.get_plan()
    if name == "find_places":
        return data.find_places(**{k: v for k, v in args.items() if k in ("kind", "indoor", "max_walking", "limit")})
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


# ---------------------------------------------------------------- shared model orchestration
def model_turn(req: dict, data: TripData, provider: ModelProvider, started: float | None = None,
               metrics: dict | None = None, strict: bool = True) -> dict:
    """Shared orchestration. Adapters only exchange model messages; tools stay here.

    Trials call this directly, so a failed model cannot be disguised by rules.
    Legacy Gemini permits plain-text completion as before; new providers require answer.
    """
    plan = data.get_plan()  # given up front: saves a model round trip on most questions
    prompt = context_text(req) + "\n\nCurrent plan (JSON):\n" + json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
    definitions = strict_tools() if strict else TOOLS
    provider.start(SYSTEM, prompt, definitions)
    calls = []
    started = time.monotonic() if started is None else started
    metrics = metrics if metrics is not None else {}
    metrics.update({"model_requests": 0, "usage": {}, "completed_answer": False,
                    "proposal_submitted": False, "proposal_rejected": False})
    for _ in range(MAX_STEPS):
        remaining = DEADLINE_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("Model took too long")
        metrics["model_requests"] += 1
        resp = provider.complete(remaining)
        if time.monotonic() - started >= DEADLINE_SECONDS:
            raise TimeoutError("Model took too long")
        for key, value in (resp.usage or {}).items():
            metrics["usage"][key] = metrics["usage"].get(key, 0) + value
        fcs = resp.calls
        if not fcs:  # plain text instead of a tool call
            if strict:
                raise ProviderError()
            return finish(data, resp.text or "Sorry, I couldn't work that out.", None, calls)
        if strict:
            # Validate the entire batch before executing any of its calls.
            schemas = {t["name"]: t["parameters_json_schema"] for t in definitions}
            if len(fcs) > 8 or (any(fc.name == "answer" for fc in fcs) and len(fcs) != 1):
                raise ProviderError()
            for fc in fcs:
                if fc.name not in schemas or not Draft202012Validator(schemas[fc.name]).is_valid(fc.args):
                    raise ProviderError()
        results = []
        for fc in fcs:
            if time.monotonic() - started >= DEADLINE_SECONDS:
                raise TimeoutError("Model took too long")
            args = fc.args
            if fc.name == "answer":
                if strict and not args.get("reply", "").strip():
                    raise ProviderError()
                if strict and args.get("proposal"):
                    proposal = args["proposal"]
                    # The product supports replacing one stop at the same time.
                    replacement = proposal["add"][0]
                    if proposal["remove_item_ids"] != [f"{proposal['date']}@{replacement['start']}"]:
                        raise ProviderError()
                out = finish(data, args.get("reply", ""), args.get("proposal"), calls, bool(args.get("needs_clarification")))
                metrics.update(completed_answer=True, proposal_submitted=bool(args.get("proposal")),
                               proposal_rejected=bool(args.get("proposal")) and out["proposal"] is None)
                return out
            out = run_tool(data, fc.name, args)
            calls.append({"name": fc.name, "summary": SUMMARY.get(fc.name, fc.name)})
            results.append((fc, out))
        provider.add_results(results)
    if strict:
        raise ProviderError()
    return finish(data, "Sorry, that took too many steps. Could you ask in a simpler way?", None, calls, True)


def gemini_turn(req: dict, data: TripData, client=None, model: str = GEMINI_MODEL, started: float | None = None,
                think: bool = True) -> dict:
    return model_turn(req, data, GeminiProvider(model, client, think), started=started, strict=False)


def provider_settings() -> dict:
    """Explicit selection. Existing deployments keep Gemini/rules behavior."""
    provider = os.getenv("AGENT_PROVIDER", "gemini").strip().lower()
    model = (os.getenv("GROQ_MODEL", GROQ_MODELS[0]) if provider == "groq"
             else GEMINI_MODEL if provider == "gemini" else None)
    configured = provider in ("gemini", "groq", "rules")
    enabled = configured and provider != "rules" and bool(os.getenv(f"{provider.upper()}_API_KEY"))
    return {"provider": provider, "model": model, "enabled": enabled, "configured": configured}


# ---------------------------------------------------------------- rules fallback (no LLM)
WANTS = {"indoor": ["rain", "indoor", "비", "실내"],
         "low walking": ["tired", "walk", "피곤", "걷"]}


def rules_turn(req: dict, data: TripData) -> dict:
    t = req["message"]["text"].lower()
    want = next((w for w, keys in WANTS.items() if any(k in t for k in keys)), None)
    calls = [{"name": "rules", "summary": "Simple rules (AI not connected)"}]
    if want is None:
        return finish(data, "I can help with rain (indoor places) or less walking.", None, calls, True)
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
            log.warning("agent model failure: model=%s %s", model, provider_failure_summary(e))
            if getattr(e, "code", None) not in BUSY:
                raise
            busy_until[model] = time.monotonic() + BUSY_PAUSE_SECONDS
            log.info("agent turn: %s busy (%s), trying the next model", model, e.code)
            last = e
    raise last or RuntimeError("no Gemini model available")


def provider_failure_summary(error: Exception) -> str:
    """Expose a useful reason without echoing provider payloads, prompts or API keys."""
    code = getattr(error, "code", None)
    reasons = {400: "request rejected", 401: "authentication rejected", 403: "access denied",
               404: "model unavailable for this API account", 429: "quota or rate limit reached",
               500: "provider error", 503: "provider overloaded", 504: "provider timed out"}
    if isinstance(code, int):
        return f"AI unavailable (HTTP {code}: {reasons.get(code, 'provider request failed')})"
    if isinstance(error, TimeoutError):
        return "AI unavailable (request timed out)"
    return "AI unavailable (provider request failed)"


def turn(req: dict, client=None) -> dict:
    data = TripData(req.get("itinerary_id"))
    settings = provider_settings()
    provider = settings["provider"]
    if provider != "rules" and (client is not None or settings["enabled"] or not settings["configured"]):
        try:
            if not settings["configured"]:
                raise ProviderError()
            if provider == "gemini":
                return gemini_with_fallback(req, data, client)
            return model_turn(req, data, GroqProvider(settings["model"], client=client))
        except Exception as e:  # Use rules when the model request fails.
            out = rules_turn(req, data)
            summary = provider_failure_summary(e)
            log.warning("agent fallback to rules: %s", summary)
            out["tool_calls"].insert(0, {"name": "gemini_error" if provider == "gemini" else "provider_error", "summary": summary})
            return out
    return rules_turn(req, data)
