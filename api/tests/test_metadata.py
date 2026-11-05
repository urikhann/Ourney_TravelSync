"""Missing values from the CSV ("NULL") become None, real values stay unchanged."""
from app.metadata import clean_meta, clean_value


def test_null_text_becomes_none():
    assert clean_meta({"a": "NULL", "b": "", "c": " null ", "d": None}) == {"a": None, "b": None, "c": None, "d": None}


def test_real_values_are_kept():
    assert clean_meta({"food_type": "한식", "opening_time": "10:00"}) == {"food_type": "한식", "opening_time": "10:00"}
    assert clean_value("NULLABLE") == "NULLABLE" and clean_meta(None) == {}


import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.import_places import import_rows, ImportProblem
from app.models import Poi, PoiMetadata
from app.places import load_places
from .test_import import row, COLS


def test_sidecar_join_pins_version_and_preserves_missing_metadata():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        import_rows(db, [{**row('a'), 'description_summary': 'old menu'}], COLS, 'v1', 'rule_v2')
        import_rows(db, [{**row('a'), 'description_summary': 'new menu'}, row('b')], COLS, 'v2', 'rule_v2')
        assert load_places(db, 'v1', ['a'])['a']['enriched_metadata']['description_summary'] == 'old menu'
        assert load_places(db, 'v2', ['a'])['a']['enriched_metadata']['description_summary'] == 'new menu'
        assert db.get(PoiMetadata, ('a', 'v1')) is not None
        assert db.get(Poi, ('a', 'v1'))._legacy_enriched_metadata == {}
        assert db.get(PoiMetadata, ('b', 'v2')) is None
        assert load_places(db, 'v2', ['b'])['b']['enriched_metadata'] == {}
        with pytest.raises(ImportProblem, match='dataset_version'):
            import_rows(db, [{**row('a'), 'dataset_version': 'wrong'}], COLS, 'v3', 'rule_v2')



def test_api_metadata_import_validates_before_writes(tmp_path):
    import csv
    from app.import_metadata import import_metadata
    from app.db import ensure_enriched_metadata_column
    from sqlalchemy.exc import IntegrityError
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    ensure_enriched_metadata_column(engine)
    path = tmp_path / 'metadata.csv'
    with Session(engine) as db:
        # Existing POIs may lack enrichment entirely.
        import_rows(db, [row('a')], COLS, 'v1', 'rule_v2')
        assert db.get(PoiMetadata, ('a', 'v1')) is None
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['place_id', 'dataset_version', 'food_type'])
            writer.writeheader(); writer.writerow(dict(place_id='a', dataset_version='wrong', food_type='seafood'))
        with pytest.raises(ImportProblem, match='dataset_version'):
            import_metadata(db, path, 'v1', expected_count=1)
        path.write_text('place_id,dataset_version,food_type\na,v1,seafood\n', encoding='utf-8')
        assert import_metadata(db, path, 'v1', expected_count=1, validate_only=True)['imported_rows'] == 0
        assert db.get(PoiMetadata, ('a', 'v1')) is None
        assert import_metadata(db, path, 'v1', expected_count=1)['imported_rows'] == 1
        db.expire_all()
        assert load_places(db, 'v1', ['a'])['a']['enriched_metadata'] == {'food_type': 'seafood'}
        metadata = db.get(PoiMetadata, ('a', 'v1'))
        metadata.metadata_json = {'food_type': 'overwrite'}
        with pytest.raises(IntegrityError, match='METADATA_IMMUTABLE'):
            db.commit()
        db.rollback()
