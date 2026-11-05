"""The onboarding survey ("travel tastes"): multiple-choice questions asked once per user, after sign-up.

It does NOT replace the 10-feature 1–5 survey (that one is per trip and drives GFAR / AWM). It adds detail:
  - keywords      -> for the TF-IDF personal recommender (Method C), matched against place descriptions
  - avoid options -> some turn on an existing hard exclusion rule (hiking, raw_fish, alcohol);
                     the others are soft dislikes, sent as avoid_keywords only (they never remove places)

Codes are stable (stored in the database). Labels and keywords can be changed freely.
"""
TASTES_VERSION = "taste-1.0"
MAX_PICKS = 5


def _o(code, ko, en, keywords, exclusion=None):
    return {"code": code, "label_ko": ko, "label_en": en, "keywords": keywords, "exclusion": exclusion}


QUESTIONS = [
    {"code": "food", "label_ko": "좋아하는 음식 종류는?", "label_en": "What food do you like?", "options": [
        _o("korean", "한식", "Korean", ["한식", "국밥", "백반", "한정식"]),
        _o("japanese", "일식", "Japanese", ["일식", "초밥", "라멘", "돈카츠"]),
        _o("chinese", "중식", "Chinese", ["중식", "짜장면", "짬뽕"]),
        _o("western", "양식", "Western", ["양식", "파스타", "피자", "스테이크"]),
        _o("seafood", "해산물", "Seafood", ["해산물", "해물", "생선", "조개"]),
        _o("meat_bbq", "고기·구이", "Meat & BBQ", ["고기", "구이", "갈비", "삼겹살"]),
        _o("street_food", "분식", "Street food", ["분식", "떡볶이", "어묵"]),
        _o("dessert", "디저트·베이커리", "Desserts & bakery", ["디저트", "베이커리", "빵", "케이크"]),
        _o("coffee", "카페", "Coffee", ["카페", "커피"]),
    ]},
    {"code": "places", "label_ko": "선호하는 여행 장소는?", "label_en": "Where do you like to go?", "options": [
        _o("sea", "바다·해변", "Sea & beaches", ["바다", "해변", "해수욕장", "해안"]),
        _o("mountain", "산·숲", "Mountains & forests", ["숲", "산림", "수목원"]),
        _o("park", "공원·산책로", "Parks & walking paths", ["공원", "산책로", "산책"]),
        _o("museum", "박물관·전시관", "Museums & galleries", ["박물관", "전시관", "미술관"]),
        _o("market", "전통시장", "Traditional markets", ["시장", "전통시장"]),
        _o("heritage", "역사·사찰", "History & temples", ["역사", "사찰", "문화재", "유적"]),
        _o("cafe_street", "카페거리", "Café streets", ["카페거리", "카페"]),
        _o("night_view", "야경 명소", "Night views", ["야경", "전망대", "야간"]),
    ]},
    {"code": "activities", "label_ko": "좋아하는 여행 활동은?", "label_en": "What do you like to do?", "options": [
        _o("food_tour", "맛집 탐방", "Food tours", ["맛집", "식당", "음식"]),
        _o("shopping", "쇼핑", "Shopping", ["쇼핑", "백화점", "상점"]),
        _o("exhibition", "전시 관람", "Exhibitions", ["전시", "관람", "갤러리"]),
        _o("hands_on", "액티비티·체험", "Activities & hands-on", ["체험", "액티비티", "레저"]),
        _o("spa", "온천·휴식", "Spa & rest", ["온천", "스파", "휴식"]),
        _o("photo", "사진 명소", "Photo spots", ["포토존", "사진", "경관"]),
        _o("festival", "축제·공연", "Festivals & shows", ["축제", "공연", "행사"]),
    ]},
    {"code": "avoid", "label_ko": "피하고 싶은 것은?", "label_en": "What do you want to avoid?", "options": [
        _o("hiking", "등산", "Hiking", ["등산", "등산로", "트레킹"], exclusion="hiking"),
        _o("long_walk", "오래 걷기", "Long walks", ["둘레길", "걷기", "도보"]),
        _o("outdoor", "야외 활동", "Outdoor activities", ["야외"]),
        _o("crowded", "사람 많은 곳", "Crowded places", ["번화가", "인파"]),
        _o("raw_fish", "날것(회)", "Raw fish", ["회", "횟집", "초밥"], exclusion="raw_fish"),
        _o("alcohol", "술집", "Bars & pubs", ["술집", "주점", "포차"], exclusion="alcohol"),
    ]},
]

OPTIONS = {q["code"]: {o["code"]: o for o in q["options"]} for q in QUESTIONS}


def check_answers(answers: dict) -> dict[str, list[str]]:
    """Unknown question / option, repeats or more than MAX_PICKS -> ValueError. Returns all 4 keys, in a fixed order."""
    unknown_q = [k for k in answers if k not in OPTIONS]
    if unknown_q:
        raise ValueError(f"unknown questions {unknown_q}; allowed: {list(OPTIONS)}")
    out = {}
    for q, opts in OPTIONS.items():
        picked = list(answers.get(q) or [])
        bad = [c for c in picked if c not in opts]
        if bad:
            raise ValueError(f"{q}: unknown options {bad}")
        if len(set(picked)) != len(picked):
            raise ValueError(f"{q}: the same option was picked twice")
        if len(picked) > MAX_PICKS:
            raise ValueError(f"{q}: pick at most {MAX_PICKS}")
        out[q] = [c for c in opts if c in picked]  # catalog order
    return out


def profile(answers: dict | None) -> dict:
    """What the rest of the system uses: keywords (likes), avoid_keywords (dislikes), exclusion_tags (hard rules)."""
    answers = answers or {}
    likes = [OPTIONS[q][c] for q in ("food", "places", "activities") for c in answers.get(q, []) if c in OPTIONS[q]]
    avoid = [OPTIONS["avoid"][c] for c in answers.get("avoid", []) if c in OPTIONS["avoid"]]
    return {
        "keywords": list(dict.fromkeys(k for o in likes for k in o["keywords"])),
        "avoid_keywords": list(dict.fromkeys(k for o in avoid for k in o["keywords"])),
        "exclusion_tags": [o["exclusion"] for o in avoid if o["exclusion"]],
    }
