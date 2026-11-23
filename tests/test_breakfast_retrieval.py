"""Breakfast retrieval retains scored alternatives without changing GFAR."""
from copy import deepcopy
import json

import numpy as np
import pytest

pytest.importorskip("pydantic")
from ourney_travelsync.common import GroupInput
from ourney_travelsync.features import FEATURE_NAMES
from ourney_travelsync.recommenders.itinerary_candidates import select_days
from ourney_travelsync.optimizer.contracts import DailyComposition
from ourney_travelsync.breakfast_metadata import breakfast_metadata
from optimizer_fixtures import DAY, fact, STAMP


def test_low_scoring_breakfast_alternative_reserved_and_exclusions_remain_upstream():
    ids = tuple(f"p{i}" for i in range(40))
    features = np.zeros((40, len(FEATURE_NAMES)))
    features[:, FEATURE_NAMES.index("cafe")] = 1
    data = GroupInput(("u",), ids, np.full((1, 10), 3.), features,
                      np.array([[5.] * 39 + [1.]]))
    records = {pid: {"latitude": 35.1, "longitude": 129.1, "duration_min": 30,
        "field_status": {"duration": "KNOWN"}, "cafe": 1} for pid in ids}
    records["p39"]["enriched_metadata"] = {"breakfast_category": fact("brunch_cafe"),
        "breakfast_hours": {DAY: fact([dict(start_min=420, end_min=600)])}}
    point = dict(latitude=35.1, longitude=129.1)
    days = [dict(date=DAY, start_min=420, end_min=600, start_point=point, end_point=point)]
    composition = DailyComposition(breakfast_enabled=True).model_dump(mode="json")
    scores = data.individual_scores.copy()
    subset, audit = select_days(data, records, days, composition=composition)
    assert "p39" in subset.poi_ids
    for j, pid in enumerate(subset.poi_ids):
        assert subset.individual_scores[0,j] == scores[0,ids.index(pid)]
    np.testing.assert_array_equal(data.individual_scores, scores)
    again, repeated = select_days(data, records, days, composition=composition)
    assert again.poi_ids == subset.poi_ids and repeated == audit
    composition["breakfast_enabled"] = False
    assert "p39" not in select_days(data, records, days, composition=composition)[0].poi_ids
    composition["breakfast_enabled"] = True
    records["p39"]["enriched_metadata"]["breakfast_hours"][DAY] = fact([])
    assert "p39" not in select_days(data, records, days, composition=composition)[0].poi_ids


@pytest.mark.parametrize("field", ["representative_menu", "detailed_category"])
def test_imported_explicit_menu_and_category_need_matching_field_provenance(field):
    source_key, checked_key = (("menu_source_url", "menu_checked_at") if field == "representative_menu"
                              else ("category_source_url", "category_checked_at"))
    meta = {field: "브런치", source_key: "synthetic://menu", checked_key: STAMP,
            "field_provenance_json": [{"fields": [field], "url": "synthetic://menu", "checked_at": STAMP}]}
    record = {"enriched_metadata": meta}
    assert breakfast_metadata(record)[0].known
    assert not breakfast_metadata(record)[1]  # Menu evidence never invents serving times.
    before = deepcopy(record)
    breakfast_metadata(record)
    assert record == before
    meta["field_provenance_json"][0]["fields"] = ["description_summary"]
    assert not breakfast_metadata(record)[0].known
    meta["field_provenance_json"][0]["fields"] = [field]
    meta[field] = "Coffee, toast, eggs"
    assert not breakfast_metadata(record)[0].known
    for text in ("No breakfast", "English breakfast tea", "조식 미제공"):
        meta[field] = text
        assert not breakfast_metadata(record)[0].known


def test_structured_evidence_can_be_imported_as_json_cells():
    record = {"enriched_metadata": {
        "breakfast_offering": json.dumps(fact(True)),
        "breakfast_hours": json.dumps({DAY: fact([dict(start_min=420, end_min=600)])})}}
    offering, hours = breakfast_metadata(record)
    assert offering.known and hours
    record["enriched_metadata"]["breakfast_offering"] = "not JSON"
    assert not breakfast_metadata(record)[0].known
