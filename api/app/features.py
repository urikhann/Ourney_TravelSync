"""The 10 taste features, in the fixed order agreed in TS SRS/SDD 1.1 (section 6.1 / 8.1).

Use this order everywhere: survey, database, contracts, recommender. Store by name, convert to a vector with FEATURES.
"""
FEATURE_SCHEMA_VERSION = "fs-1.0"

FEATURES = ["food", "cafe", "nature", "history", "art", "shopping", "activity", "entertainment", "relax", "festival"]

# What the survey shows (English for the demo, Korean from the SRS table).
LABELS = {
    "food": ("Food & local eats", "맛집과 음식"),
    "cafe": ("Cafés & desserts", "카페와 디저트"),
    "nature": ("Nature & views", "자연과 경관"),
    "history": ("History & tradition", "역사와 전통"),
    "art": ("Art, exhibitions & shows", "예술과 전시 및 공연"),
    "shopping": ("Shopping", "쇼핑"),
    "activity": ("Activities & experiences", "체험과 액티비티"),
    "entertainment": ("Fun & entertainment", "놀이와 엔터테인먼트"),
    "relax": ("Rest & walks", "휴식과 산책"),
    "festival": ("Festivals & events", "축제와 이벤트"),
}

# Personal "never take me there" rules (member exclusions, SDD 6.6). Tags are derived from the place category at import.
EXCLUSION_TAGS = {
    "hiking": "No hiking / mountain trails",
    "raw_fish": "No raw fish (회, 초밥)",
    "alcohol": "No bars or pubs",
}
