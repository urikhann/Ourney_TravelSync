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
    assert sum(row["description_summary"] != "NULL" for row in places.records) == 2252
    assert "field_provenance_json" in places.records[0]


def test_v3_preserves_core_and_existing_descriptions():
    previous = files('ourney_travelsync').joinpath('data', 'travelsync_poi_metadata_enriched_v1.csv')
    with previous.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        old_rows = list(reader)
    new_rows = load_places().records
    assert len(old_rows) == len(new_rows)
    changed_columns = {
        'description_raw', 'description_summary', 'description_source_url',
        'description_source_type', 'description_basis', 'description_raw_format',
        'description_checked_at', 'field_provenance_json',
    }
    additions = 0
    for old, new in zip(old_rows, new_rows):
        assert list(new) == columns
        assert old['place_id'] == new['place_id']
        assert all(old[key] == new[key] for key in columns if key not in changed_columns)
        if old['description_summary'] != 'NULL':
            assert all(old[key] == new[key] for key in changed_columns)
        elif new['description_summary'] != 'NULL':
            additions += 1
    assert additions == 417
