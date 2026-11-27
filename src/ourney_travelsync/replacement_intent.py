"""Conservative replacement intent extraction shared by rules and the API."""
import re


def extract_replacement(text: str) -> dict | None:
    text = re.sub(r"^\s*@(?:travelsync|ai|ts)\s*", "", text, flags=re.I).strip(" .?!")
    if re.match(r"^(?:why\b|what\b|왜\s)", text, re.I):
        return None
    patterns = (
        r"^(?P<target>.+?)\s*가기\s*싫어(?P<rest>.*(?:치환|바꿔|대체|다른).*)",
        r"^(?P<target>.+?)\s*(?:대신|빼고)\s*(?P<rest>.+)$",
        r"^(?P<target>.+?)(?:을|를)\s*(?P<kind>카페|식당|cafe|restaurant)(?:로|으로)\s*(?:바꿔|치환|대체)",
        r"^(?:please\s+)?(?:replace|swap)\s+(?P<target>.+?)(?:\s+(?:with|for)\s+(?P<rest>.+))?$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        values = match.groupdict()
        rest = values.get("kind") or values.get("rest") or ""
        constraints = {}
        if re.search(r"카페|\bcaf[eé]\b", rest, re.I):
            constraints["category"] = "cafe"
        elif re.search(r"식당|\brestaurant\b", rest, re.I):
            constraints["category"] = "meal"
        if rest and not constraints.get("category"):
            if re.match(r"^(?:please\s+)?(?:replace|swap)\b", text, re.I):
                if not re.fullmatch(r"(?:a |an |some |a different |another )?(?:place|location)|somewhere else|something else", rest, re.I):
                    constraints["replacement_poi_name"] = rest.strip(" .?!")
            elif "다른" not in rest:
                named = re.fullmatch(r"(.+?)\s*(?:넣어줘|넣어주세요|추천해줘|추천해주세요)[.!?]*", rest.strip())
                if named:
                    constraints["replacement_poi_name"] = named[1].strip()
        # Existing itinerary constraints remain authoritative. New conditions that
        # lack a verified input contract must be clarified, never silently ignored.
        if re.search(r"휠체어|접근성|실내|덜\s*걷|예산|저렴|\d+\s*원|wheelchair|accessible|indoor|less walking|budget|cheap|under\s*\d", rest, re.I):
            constraints["needs_clarification"] = True
        return {"intent": "replace_poi", "target_poi_name": values["target"].strip(),
                "replacement_constraints": constraints}
    if re.search(r"치환해|바꿔줘|바꿔주세요|다른\s*(?:곳|장소).*(?:추천|넣어)|^(?:please\s+)?(?:replace|swap)\b", text, re.I):
        return {"intent": "replace_poi", "target_poi_name": "", "replacement_constraints": {}}
    return None


def resolve_target(intent: dict, days: list[dict], names: dict[str, list[str]]) -> tuple[dict | None, str | None]:
    """Return an exact scheduled occurrence, never choose the first ambiguous name."""
    query = intent["target_poi_name"].casefold().strip()
    requested_date = re.search(r"\d{4}-\d{2}-\d{2}", query)
    requested_time = re.search(r"\d{1,2}:\d{2}", query)
    for found in (requested_date, requested_time):
        if found:
            query = query.replace(found.group(), "").strip()
    matches, exact = [], []
    for day in days:
        if requested_date and day["date"] != requested_date.group():
            continue
        for item in day["items"]:
            if requested_time and item["start"] != requested_time.group().zfill(5):
                continue
            aliases = [n.casefold() for n in names.get(item["poi_id"], []) if n]
            hit = {"date": day["date"], "item": item, "item_id": f"{day['date']}@{item['start']}"}
            if query and any(query in n for n in aliases):
                matches.append(hit)
            if query in aliases:
                exact.append(hit)
    matches = exact or matches
    if len(matches) != 1:
        return None, "ambiguous_target_poi" if matches else "target_poi_not_found"
    return matches[0], None
