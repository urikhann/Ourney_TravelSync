"""Explicit single-place settings requests shared by chat and agent fallbacks."""
import re
from decimal import Decimal


def extract_settings(text):
    value = re.sub(r"^\s*@(?:travelsync|ai|ts)\s*", "", text, flags=re.I).strip(" .!?。")
    patterns = (
        r"^(?:please\s+)?(?:set|change|adjust)\s+(?:the\s+)?(?:visit\s+)?duration\s+(?:of|for|at)\s+(?P<target>.+?)\s+to\s+(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>minutes?|mins?|hours?|hrs?)$",
        r"^(?:please\s+)?(?:set|change|adjust)\s+(?P<target>.+?)(?:'s)?\s+(?:visit\s+)?(?:duration|time)\s+to\s+(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>minutes?|mins?|hours?|hrs?)$",
        r"^(?:please\s+)?spend\s+(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>minutes?|mins?|hours?|hrs?)\s+(?:at|in)\s+(?P<target>.+)$",
        r"^(?P<target>.+?)(?:의)?\s*방문\s*시간(?:을|를)?\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>분|시간)(?:으로|로)?\s*(?:바꿔\s*줘|바꿔\s*주세요|변경해\s*줘|변경해\s*주세요|설정해\s*줘|설정해\s*주세요|해\s*줘|해\s*주세요)$",
        r"^(?P<target>.+?)에서\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>분|시간)\s*(?:머물게\s*해\s*줘|머물게\s*해\s*주세요|머무르게\s*해\s*줘|머무르게\s*해\s*주세요)$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, value, re.I)
        if match:
            minutes = Decimal(match["amount"]) * (60 if match["unit"].lower().startswith(("h", "시간")) else 1)
            if minutes != minutes.to_integral_value() or not 1 <= minutes <= 1440:
                return {"action_type": "set_visit_duration", "target_name": match["target"].strip(), "duration_min": None}
            return {"action_type": "set_visit_duration", "target_name": match["target"].strip(), "duration_min": int(minutes), "constraints": {}}
    for locked, pattern in (
        (False, r"^(?:(?:please|can you|could you)\s+)?unlock\s+(?P<target>.+)$"),
        (True, r"^(?:(?:please|can you|could you)\s+)?lock\s+(?P<target>.+)$"),
        (False, r"^(?P<target>.+?)(?:의)?\s*(?:잠금(?:을)?\s*해제|고정(?:을)?\s*해제)(?:해\s*줘|해\s*주세요)$"),
        (False, r"^(?P<target>.+?)(?:의)?\s*잠금(?:을)?\s*풀어\s*(?:줘|주세요)$"),
        (True, r"^(?P<target>.+?)(?:을|를)?\s*(?:잠가\s*줘|잠가\s*주세요|잠궈\s*줘|고정해\s*줘|고정해\s*주세요|잠금해\s*줘|잠금해\s*주세요)$"),
    ):
        match = re.fullmatch(pattern, value, re.I)
        if match:
            return {"action_type": "set_place_lock", "target_name": match["target"].strip(), "locked": locked, "constraints": {}}
    return None
