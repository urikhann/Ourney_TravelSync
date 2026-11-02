"""Missing values from the CSV ("NULL") become None, real values stay unchanged."""
from app.metadata import clean_meta, clean_value


def test_null_text_becomes_none():
    assert clean_meta({"a": "NULL", "b": "", "c": " null ", "d": None}) == {"a": None, "b": None, "c": None, "d": None}


def test_real_values_are_kept():
    assert clean_meta({"food_type": "한식", "opening_time": "10:00"}) == {"food_type": "한식", "opening_time": "10:00"}
    assert clean_value("NULLABLE") == "NULLABLE" and clean_meta(None) == {}
