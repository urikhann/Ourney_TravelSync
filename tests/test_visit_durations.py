import pytest

from ourney_travelsync.visit_durations import default_duration, named_duration


@pytest.mark.parametrize("name", ["해운대온천", "부산 찜질방", "온천 찜질방", "온천장", "가족온천탕"])
def test_spa_names_get_three_hours_regardless_of_category(name):
    assert named_duration(name) == 180
    assert default_duration({"relax": 1}, name) == 180
    assert default_duration({"cafe": 1}, name) == 180


@pytest.mark.parametrize("name", [None, "", "해운대 해수욕장", "부산 스파"])
def test_other_names_keep_existing_category_defaults(name):
    assert named_duration(name) is None
    assert default_duration({"cafe": 1}, name) == 45
    assert default_duration({"history": 1}, name) == 90
    assert default_duration({"relax": 1}, name) == 60
