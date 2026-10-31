"""CSV columns shared by optional import/collection components."""
from .features import FEATURE_NAMES
FEATURES = list(FEATURE_NAMES)
BASE = ['place_id', 'place_name', 'category', 'category_name', 'cat1', 'cat2', 'cat3', 'address', 'road_address', 'latitude', 'longitude', 'phone', 'place_url', 'image_url', 'source']
EXTRA = ['source_place_id', 'kakao_place_ids', 'feature_rules', 'exclude_reason']
COLUMNS = BASE + EXTRA + FEATURES

# Optional enrichment columns; the collector still produces the original core schema.
ENRICHED_COLUMNS = [
    'detailed_category_raw',
    'detailed_category',
    'category_source_url',
    'description_raw',
    'description_summary',
    'description_source_url',
    'description_source_type',
    'food_type',
    'representative_menu',
    'menu_raw',
    'menu_source_url',
    'representative_activity',
    'activity_raw',
    'activity_source_url',
    'detail_source_type',
    'detail_source_url',
    'detail_checked_at',
    'detail_verification_status',
    'lazy_enrichment_priority',
    'description_basis',
    'description_raw_format',
    'food_type_source_url',
    'category_checked_at',
    'description_checked_at',
    'menu_checked_at',
    'activity_checked_at',
    'field_provenance_json',
    'metadata_match_notes',
    'metadata_collection_status',
    'lazy_enrichment_reason',
    'opening_time',
    'closing_time',
    'closed_day',
    'break_times_raw',
    'last_order_raw',
    'hours_scope',
    'hours_valid_from',
    'hours_valid_until',
    'collection_status',
    'operating_source_url',
    'checked_at',
    'operating_hours_raw',
    'closed_day_raw',
]
