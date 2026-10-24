"""CSV columns shared by optional import/collection components."""
from .features import FEATURE_NAMES
FEATURES = list(FEATURE_NAMES)
BASE = ['place_id', 'place_name', 'category', 'category_name', 'cat1', 'cat2', 'cat3', 'address', 'road_address', 'latitude', 'longitude', 'phone', 'place_url', 'image_url', 'source']
EXTRA = ['source_place_id', 'kakao_place_ids', 'feature_rules', 'exclude_reason']
COLUMNS = BASE + EXTRA + FEATURES
