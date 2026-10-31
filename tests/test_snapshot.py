import csv
import hashlib
from collections import Counter
from importlib.resources import files
import numpy as np
from ourney_travelsync import load_places, FEATURE_NAMES
from ourney_travelsync.places import OFFICIAL_FILENAME, OFFICIAL_SHA256


def test_official_enriched_snapshot():
    resource = files('ourney_travelsync').joinpath('data', OFFICIAL_FILENAME)
    assert hashlib.sha256(resource.read_bytes()).hexdigest() == OFFICIAL_SHA256
    with resource.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        assert len(reader.fieldnames) == 72
        assert tuple(reader.fieldnames[19:29]) == FEATURE_NAMES
        rows = list(reader)
    places = load_places()
    assert len(rows) == len(set(places.place_ids)) == 3570
    assert places.features.shape == (3570, 10)
    assert np.isin(places.features, [0, 1]).all()
    assert (places.features.sum(axis=1) > 0).all()
    assert all(r['feature_rules'] for r in rows)
    assert Counter(r['source'] for r in rows) == {'TOUR_API':345, 'TOUR_API+KAKAO':106, 'KAKAO':3119}

    assert all(len(row) == 72 for row in places.records)
    assert any(row["description_summary"] for row in places.records)
    assert "field_provenance_json" in places.records[0]
