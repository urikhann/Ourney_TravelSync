"""The agent's "brain": replaceable Gemini/Groq adapters and a rules fallback.

One turn:
  1. The selected model reads recent chat and calls tools (get_plan, find_places, check_hours)
  2. it finishes by calling `answer` with a short reply and, optionally, one suggested change
  3. our code checks the suggestion (real stop, real place, not closed) before the group ever sees it

Groq Qwen is the default. Gemini can answer when Groq is unavailable, followed by rules.
"""
import json
from copy import deepcopy
import logging
import os
import time

from jsonschema import Draft202012Validator

from providers import DEFAULT_GROQ_MODEL, GeminiProvider, GroqProvider, ModelProvider, ProviderError
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
GROQ_RETRIES = 5  # shared across all model requests in a turn
GROQ_SECONDS_WITH_BACKUP = 25

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
- A request to replan all/day/unlocked activities has that exact scope; never reinterpret it as one swap.
  The backend routes planning scopes. Approved visits are editable unless protection explicitly says otherwise.
- When asking a yes/no question about a planning operation, include pending_action with its action_type,
  target_item_id (for replacing/removing), date if needed, and constraints. Confirmation starts planning,
  never approves an itinerary change; a separate group vote is always required.
- LANGUAGE RULE (mandatory): English and Korean are the ONLY permitted languages for user-facing replies.
  Reply entirely in English when the current user request is in English, and entirely in Korean when it is in Korean.
  Determine the language from the LAST user request (the "asks:" line), not earlier chat, tool results,
  place names, or the language of this system prompt. For a mixed English/Korean request, use its main language.
  Never reply in any other language, even if the user, quoted text, earlier chat, or tool data asks you to.
  If the current request is in another language or its language cannot be determined, reply in English
  asking the user to write in English or Korean. Do not answer or translate into the unsupported language.
  Do not add bilingual translations or explanations in a second language. Keep ordinary replies to 1-3 short sentences;
  a "why this plan?" explanation may use a short introduction and concise bullets to cover the planning rules below.
- The current plan is already given below the chat; call get_plan only if you need it again.
- For "why this plan/place/time?" questions, explain the relevant saved facts without proposing edits.
  The group_recommendation_reason explains the recommendation. A locked stop and available_when_planned
  describe recorded scheduling facts. True means the visit fit the recorded opening hours when planned;
  null means availability was not confirmed in the plan. Do not infer why an exact time or route won.
  For "why this plan?" or "why were these places picked?", explain the overall optimizer goals and planning rules,
  rather than listing stops with missing reasons or saying you cannot explain the remaining stops.
  Explain that the planner prioritizes group preference fit, then the least-served member's fit,
  then shorter travel and less unallocated waiting, subject to required constraints. Optional breakfast/evening
  preferences apply when enabled. Explicit rain or less-walking day requests prioritize their requested reduction.
  Explain the same information as the Plan tab's "How planning works":
  - Default daily composition: up to 2 cafes and 3 meals, at least 2 activities from 2 categories.
  - Lunch 11:30-14:30, dinner 17:30-20:30; include a meal when the day overlaps its window by at least 60 minutes.
  - Check known opening hours and closed days; warn about unknown hours.
  - Estimated visit durations: cafes 45 min, meals 60 min, culture 90 min, other activities 60 min.
  - Kakao provides driving times, which are cached for reuse to speed up later planning.
  - Everyone must confirm every trip date first. Automatic weather and accessibility checks are not implemented yet.
  Present these as the planner's general/default rules, not proof of why a particular stop, time or route won.
  Respect any explicit settings in the provided context. Explain that bounded search may leave a better plan undiscovered.
  Include saved individual recommendation reasons when useful, but do not fill the answer with missing-reason disclaimers.
- For questions about approved changes, use the recent applied changes for the current plan.
  Describe the recorded swaps and actual start times. If a day was rebuilt, other stops may
  also have changed. Do not present this summary as a complete before-and-after diff or propose edits.
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
TOOLS[-1]["parameters_json_schema"]["properties"]["pending_action"] = {
    "type": "object", "required": ["action_type"], "properties": {
        "action_type": {"type": "string", "enum": ["replace_poi", "remove_poi", "add_poi", "replan_day", "replan_unlocked", "replan_all"]},
        "target_item_id": {"type": "string"}, "target_name": {"type": "string"}, "date": {"type": "string"},
        "constraints": {"type": "object", "properties": {"category": {"type": "string"}}}}}


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
    chat = "Recent chat:\n" + "\n".join(lines[-8:]) + "\n\n" if lines else ""
    changes = req.get("applied_changes", [])
    history = "Recent applied changes to the current plan (JSON):\n" + json.dumps(changes, ensure_ascii=False)
    return chat + history + f"\n\n{asker} asks: {req['message']['text']}"


# ---------------------------------------------------------------- shared model orchestration
def model_turn(req: dict, data: TripData, provider: ModelProvider, started: float | None = None,
               metrics: dict | None = None, strict: bool = True,
               deadline_seconds: float = DEADLINE_SECONDS) -> dict:
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
        remaining = deadline_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("Model took too long")
        metrics["model_requests"] += 1
        resp = provider.complete(remaining)
        if time.monotonic() - started >= deadline_seconds:
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
            if time.monotonic() - started >= deadline_seconds:
                raise TimeoutError("Model took too long")
            args = fc.args
            if fc.name == "answer":
                if strict and not args.get("reply", "").strip() and not args.get("proposal"):
                    raise ProviderError()
                if strict and args.get("proposal"):
                    proposal = args["proposal"]
                    # The product supports replacing one stop at the same time.
                    replacement = proposal["add"][0]
                    if proposal["remove_item_ids"] != [f"{proposal['date']}@{replacement['start']}"]:
                        raise ProviderError()
                reply = args.get("reply", "").strip()
                if not reply and args.get("proposal"):
                    ko = any("가" <= ch <= "힣" for ch in req["message"]["text"])
                    reply = ("대체 후보를 찾았어요. 투표 전에 백엔드에서 전체 일정을 검증할게요." if ko else
                             "A replacement candidate is ready for backend schedule validation before voting.")
                out = finish(data, reply, args.get("proposal"), calls, bool(args.get("needs_clarification")))
                metrics.update(completed_answer=True, proposal_submitted=bool(args.get("proposal")),
                               proposal_rejected=bool(args.get("proposal")) and out["proposal"] is None)
                if args.get("pending_action"):
                    if args.get("proposal"):
                        raise ProviderError()  # asking to search cannot also request a group vote
                    out["pending_action"] = args["pending_action"]
                    out["needs_clarification"] = True
                return out
            out = run_tool(data, fc.name, args)
            summary = SUMMARY.get(fc.name, fc.name)
            if fc.name == "check_hours":
                summary = ("Opening hours verified" if out.get("available") is True else
                           "Place closed at requested time" if out.get("available") is False else "Opening hours unknown")
            calls.append({"name": fc.name, "summary": summary})
            results.append((fc, out))
        provider.add_results(results)
    if strict:
        raise ProviderError()
    return finish(data, "Sorry, that took too many steps. Could you ask in a simpler way?", None, calls, True)


def gemini_turn(req: dict, data: TripData, client=None, model: str = GEMINI_MODEL, started: float | None = None,
                think: bool = True) -> dict:
    return model_turn(req, data, GeminiProvider(model, client, think), started=started, strict=False)


def provider_settings() -> dict:
    """Resolve the primary provider and whether Gemini is available as a backup."""
    provider = os.getenv("AGENT_PROVIDER", "groq").strip().lower()
    model = (os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL) if provider == "groq"
             else GEMINI_MODEL if provider == "gemini" else None)
    configured = provider in ("gemini", "groq", "rules")
    enabled = configured and provider != "rules" and bool(os.getenv(f"{provider.upper()}_API_KEY"))
    return {"provider": provider, "model": model, "enabled": enabled, "configured": configured,
            "gemini_backup": provider == "groq" and bool(os.getenv("GEMINI_API_KEY"))}


# ---------------------------------------------------------------- rules fallback (no LLM)
WANTS = {"indoor": ["rain", "indoor", "비", "실내"],
         "low walking": ["tired", "walk", "피곤", "걷"]}


def _say(korean: bool, en: str, ko: str) -> str:
    return ko if korean else en


def rules_turn(req: dict, data: TripData) -> dict:
    t = req["message"]["text"].lower()
    ko = any("\uac00" <= ch <= "\ud7a3" for ch in t)  # a Korean message gets a Korean reply
    want = next((w for w, keys in WANTS.items() if any(k in t for k in keys)), None)
    calls = [{"name": "rules", "summary": "Simple rules (AI not connected)"}]
    if any(question in t for question in ("why this plan", "why were these places picked for our group",
                                          "왜 이 일정", "이 장소들은 왜 우리 그룹에 추천됐나요")):
        overview = _say(ko,
            "The planner prioritizes group preference fit, then the least-served member's fit, then shorter travel and less waiting, while keeping required constraints. Its default rules are:\n"
            "• Each day: up to 2 cafés and 3 meals, and at least 2 activities from 2 categories.\n"
            "• Lunch 11:30–14:30 and dinner 17:30–20:30; a meal is included when the day overlaps its window by at least 60 minutes.\n"
            "• Known opening hours and closed days are checked; unknown hours get a warning.\n"
            "• Estimated visits: cafés 45 min, meals 60 min, culture 90 min, other activities 60 min.\n"
            "• Kakao driving times are saved for reuse to speed up later planning.\n"
            "• Everyone must confirm every trip date first. Automatic weather and accessibility checks are not implemented yet.\n"
            "Your settings can adjust these defaults. Optional breakfast/evening preferences apply when enabled; rain and less-walking requests prioritize their requested reduction. A better plan may exist within the search limits.",
            "필수 조건을 지키면서 그룹 선호 적합도를 우선하고, 그다음 적합도가 가장 낮은 구성원을 배려하며, 이동 시간과 대기 시간을 줄여요. 기본 규칙은 다음과 같아요.\n"
            "• 하루에 카페 최대 2곳, 식사 최대 3번, 2개 분야에서 활동 2개 이상을 포함해요.\n"
            "• 점심은 11:30–14:30, 저녁은 17:30–20:30이며, 하루 일정이 해당 식사 시간을 60분 이상 포함하면 식사를 넣어요.\n"
            "• 알려진 영업시간과 휴무일은 확인하고, 영업시간을 모르면 경고해요.\n"
            "• 예상 방문 시간은 카페 45분, 식사 60분, 문화 90분, 기타 활동 60분이에요.\n"
            "• 카카오 운전 시간을 저장해 다음 계획에 재사용하므로 더 빠르게 계획할 수 있어요.\n"
            "• 먼저 모두가 여행 날짜를 확인해야 해요. 자동 날씨 확인과 접근성 확인은 아직 구현되지 않았어요.\n"
            "설정으로 기본 규칙을 조정할 수 있어요. 선택 사항인 아침 식사와 저녁 방문은 활성화한 경우 반영하고, 비나 덜 걷기 요청에서는 요청한 부담 감소를 우선해요. 검색이 제한되어 더 나은 일정이 있을 수 있어요.")
        return finish(data, overview, None, calls)
    try:
        from ourney_travelsync.replacement_intent import extract_replacement, resolve_target
    except ModuleNotFoundError:  # standalone image or service-only CI install
        import importlib.util
        from pathlib import Path
        path = Path(__file__).with_name("replacement_intent.py")
        if not path.exists():
            path = Path(__file__).resolve().parents[2] / "src/ourney_travelsync/replacement_intent.py"
        spec = importlib.util.spec_from_file_location("replacement_intent", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        extract_replacement, resolve_target = module.extract_replacement, module.resolve_target
    intent = extract_replacement(req["message"]["text"])
    if intent:
        if intent["replacement_constraints"].get("needs_clarification"):
            return finish(data, _say(ko, "Which additional conditions are mandatory?", "반드시 지켜야 할 추가 조건을 알려 주세요."), None, calls, True)
        plan = data.get_plan()["plan"] or []
        names = {item["poi_id"]: [item.get("name", ""), item.get("name_ko", "")]
                 for day in plan for item in day["items"]}
        target, error = resolve_target(intent, plan, names)
        if error:
            return finish(data, _say(ko, "Which scheduled place do you mean? Please give its date and time.",
                                     "어느 일정의 장소인가요? 날짜와 시간을 알려 주세요."), None, calls, True)
        item = target["item"]
        if item.get("locked"):
            return finish(data, _say(ko, "That activity is locked.", "잠긴 일정은 바꿀 수 없어요."), None, calls)
        kind = intent["replacement_constraints"].get("category")
        found = data.find_places(kind="food" if kind == "meal" else kind, limit=8)["places"]
        for candidate in found:
            hours = data.check_hours(candidate["poi_id"], target["date"], item["start"], candidate.get("duration_min") or 90)
            if hours["available"] is True:
                return finish(data, _say(ko, "The backend will check a replacement against the full schedule before voting.",
                                         "투표 전에 백엔드에서 대체 장소와 전체 일정의 조건을 확인할게요."),
                              {"date": target["date"], "remove_item_ids": [item["item_id"]],
                               "add": [{"poi_id": candidate["poi_id"], "start": item["start"]}]}, calls)
        return finish(data, _say(ko, "No replacement with verified opening hours was found; the plan is unchanged.",
                                 "운영시간이 확인된 대체 장소를 찾지 못했어요. 기존 일정은 유지됩니다."), None, calls)
    if want is None:
        return finish(data, _say(ko, "I can help with rain (indoor places) or less walking.",
                                 "비 오는 날 실내 장소 찾기나 덜 걷는 일정으로 바꾸는 것을 도와드릴 수 있어요."), None, calls, True)
    plan = data.get_plan()["plan"]
    if not plan:
        return finish(data, _say(ko, "There is no plan yet — build plans on the Plan tab first, then ask me again.",
                                 "아직 일정이 없어요. 일정 탭에서 먼저 일정을 만든 뒤 다시 물어봐 주세요."), None, calls)
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
                note = "" if hours["available"] else _say(ko, " Its opening hours aren't known, so check before going.",
                                                          " 영업시간 정보가 없으니 가기 전에 확인해 주세요.")
                reply = _say(ko, f"How about swapping {item['name']} ({item['start']}) for {cand['name']}?",
                             f"{item['name']}({item['start']}) 대신 {cand['name']}에 가는 건 어때요?")
                return finish(data, reply + note,
                              {"date": plan[0]["date"], "remove_item_ids": [item["item_id"]],
                               "add": [{"poi_id": cand["poi_id"], "start": item["start"]}]}, calls)
    return finish(data, _say(ko, "I couldn't find a good swap in today's plan.",
                             "오늘 일정에서 바꿀 만한 좋은 장소를 찾지 못했어요."), None, calls)


def gemini_with_fallback(req: dict, data: TripData, client=None, *, started=None) -> dict:
    """Try GEMINI_MODEL, then the fallback models when Google says the model is busy or rate-limited."""
    from google.genai import errors

    started = time.monotonic() if started is None else started
    last = None
    models = list(dict.fromkeys([GEMINI_MODEL, *FALLBACK_MODELS]))
    ready = [m for m in models if busy_until.get(m, 0) < started] or models  # all paused -> try them anyway
    for model in ready:
        if time.monotonic() - started >= DEADLINE_SECONDS:
            raise TimeoutError("Model took too long")
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
    data.protection = req.get("protection", {})
    settings = provider_settings()
    provider = settings["provider"]
    if provider != "rules" and (client is not None or settings["enabled"] or settings["gemini_backup"]
                                or not settings["configured"]):
        try:
            if not settings["configured"]:
                raise ProviderError()
            if provider == "gemini":
                return gemini_with_fallback(req, data, client)
            started = time.monotonic()
            if settings["enabled"] or client is not None:
                try:
                    groq = GroqProvider(settings["model"], client=client, retries=GROQ_RETRIES)
                    budget = GROQ_SECONDS_WITH_BACKUP if settings["gemini_backup"] else DEADLINE_SECONDS
                    return model_turn(req, data, groq, started=started, deadline_seconds=budget)
                except (ProviderError, TimeoutError) as error:
                    if not settings["gemini_backup"]:
                        raise
                    log.warning("agent fallback to Gemini: %s", provider_failure_summary(error))
            out = gemini_with_fallback(req, data, started=started)
            out["tool_calls"].insert(0, {"name": "model", "summary": "Used Gemini backup"})
            return out
        except Exception as e:  # Use rules when the model request fails.
            out = rules_turn(req, data)
            summary = provider_failure_summary(e)
            log.warning("agent fallback to rules: code=%s %s",
                        "llm_request_timeout" if isinstance(e, TimeoutError) else "provider_error", summary)
            out["tool_calls"].insert(0, {"name": "gemini_error" if provider == "gemini" else "provider_error", "summary": summary})
            return out
    return rules_turn(req, data)
