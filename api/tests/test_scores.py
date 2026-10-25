from app.scores import to_fit


def test_one_to_five_becomes_zero_to_one():
    assert to_fit(1, "1-5") == 0 and to_fit(5, "1-5") == 1 and to_fit(3, "1-5") == 0.5
    assert to_fit(4.667, "1-5") == 0.9167


def test_old_scale_and_missing_values_are_unchanged():
    assert to_fit(0.42, "0-1") == 0.42 and to_fit(0.42, None) == 0.42 and to_fit(None, "1-5") is None
