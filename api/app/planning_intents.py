"""Explicit scope parsing. Full replanning never degrades to a single swap."""
import re

from ourney_travelsync.replacement_intent import extract_replacement


SCOPES = {"replace_poi", "remove_poi", "add_poi", "replan_day", "replan_unlocked", "replan_all"}


def clean(text):
    return re.sub(r"^\s*@(?:travelsync|ai|ts)\s*", "", text, flags=re.I).strip(" .!?。")


def confirmation(text):
    value = " ".join(clean(text).casefold().replace(",", " ").split())
    if value in {"yes", "yes please", "y", "ok", "okay", "sure", "go ahead", "please do", "sounds good",
                 "네", "예", "응", "좋아요", "좋습니다", "그래", "그래요", "진행해줘", "진행해주세요", "네 진행해주세요"}:
        return True
    if value in {"no", "no thanks", "no thank you", "nope", "not now", "n", "cancel", "아니", "아니요", "아니오", "아뇨", "취소", "취소해줘", "하지마"}:
        return False
    return None


def unsupported_constraints(action):
    constraints = action.get("constraints") or {}
    if not isinstance(constraints, dict) or constraints.get("needs_clarification"):
        return True
    if action["action_type"] != "replace_poi":
        if action["action_type"] == "replan_day" and set(constraints) == {"day_preference"}:
            return constraints["day_preference"] not in ("indoor", "less_walking")
        return bool(constraints)
    if set(constraints)-{"category", "replacement_poi_name", "needs_clarification"}:
        return True
    return ("category" in constraints and constraints["category"] not in ("cafe", "meal")) or (
        "replacement_poi_name" in constraints and not isinstance(constraints["replacement_poi_name"], str))


def extract(text):
    value = clean(text)
    if re.match(r"^(?:why|what)\b|^왜\s", value, re.I):
        return None
    when = re.search(r"\d{4}-\d{2}-\d{2}", value)
    day = when.group() if when else None
    # Exact shortcut messages (including previously saved versions) have a
    # deterministic day scope, independent of model availability or chat history.
    shortcuts = {
        "it's raining — can we swap outdoor stops for indoor places": "indoor",
        "it's raining — please replan today's whole day to minimize outdoor activities": "indoor",
        "we're tired — please make today's plan need less walking": "less_walking",
        "we're tired — please replan today's whole day to minimize walking": "less_walking",
        "비가 와요 — 야외 장소를 실내로 바꿀 수 있을까요": "indoor",
        "비가 와요 — 오늘 하루 전체 일정을 야외 활동이 최소가 되도록 다시 계획해 주세요": "indoor",
        "너무 피곤해요 — 오늘 일정은 덜 걷게 바꿔 주세요": "less_walking",
        "너무 피곤해요 — 오늘 하루 전체 일정을 걷기가 최소가 되도록 다시 계획해 주세요": "less_walking",
    }
    preference = shortcuts.get(value.casefold())
    if preference:
        return dict(action_type="replan_day", date="today", constraints={"day_preference": preference})
    if re.search(r"re[- ]?plan|plan (?:it |everything )?(?:again|from scratch)|다시\s*(?:계획|짜|만들)|재계획|일정.*새로", value, re.I):
        full = bool(re.search(r"\ball\b|\bentire\b|\bwhole\b|from scratch|전체|처음부터", value, re.I))
        scope = "replan_unlocked" if re.search(r"unlocked|flexible|잠기지|잠금.*제외|유동", value, re.I) else "replan_all" if full else "replan_day" if day or re.search(r"\bday\b|해당\s*날짜|하루", value, re.I) else "replan_all"
        additional = bool(re.search(r"\b(?:avoid(?:ing)?|exclud(?:e|ing)|budget|wheelchair|cheap|with|without|before|after|only|must)\b|제외해|예산|휠체어|저렴|실내|포함|이하|\d+\s*원", value, re.I)) or bool(full and day)
        return dict(action_type=scope, date=day, constraints={"needs_clarification": True} if additional else {})
    replacement = extract_replacement(value)
    if replacement:
        return dict(action_type="replace_poi", target_name=replacement["target_poi_name"],
                    date=day, constraints=replacement["replacement_constraints"])
    for scope, pattern in (
        ("remove_poi", r"^(?:please\s+)?(?:remove|delete)\s+(.+)$"),
        ("remove_poi", r"^(.+?)(?:을|를)?\s*(?:빼줘|삭제해줘|제거해줘)$"),
        ("add_poi", r"^(?:please\s+)?add\s+(.+?)(?:\s+on\s+\d{4}-\d{2}-\d{2})?$"),
        ("add_poi", r"^(.+?)(?:을|를)?\s*(?:추가해줘|넣어줘)$"),
    ):
        match = re.match(pattern, value, re.I)
        if match:
            name = match[1].strip()
            if day:
                name = name.replace(day, "").strip()
            return dict(action_type=scope, target_name=name, date=day, constraints={})
    return None
