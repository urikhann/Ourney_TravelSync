import pytest

from ourney_travelsync.planning_settings_intent import extract_settings


@pytest.mark.parametrize("text,name,minutes", [
    ("Set the visit duration for Busan Museum to 90 minutes", "Busan Museum", 90),
    ("Change Busan Museum's visit duration to 2 hours", "Busan Museum", 120),
    ("Spend 1.5 hours at Busan Museum", "Busan Museum", 90),
    ("@ai 부산박물관 방문 시간을 90분으로 바꿔줘", "부산박물관", 90),
    ("부산박물관의 방문시간을 2시간으로 설정해주세요", "부산박물관", 120),
    ("부산박물관에서 1.5시간 머물게 해줘", "부산박물관", 90),
])
def test_explicit_duration_requests(text, name, minutes):
    action = extract_settings(text)
    assert action["action_type"] == "set_visit_duration"
    assert action["target_name"] == name
    assert action["duration_min"] == minutes


@pytest.mark.parametrize("text,name,locked", [
    ("Lock Busan Museum", "Busan Museum", True),
    ("Please unlock Busan Museum", "Busan Museum", False),
    ("Can you unlock Busan Museum?", "Busan Museum", False),
    ("부산박물관을 잠가줘", "부산박물관", True),
    ("부산박물관 잠금 해제해줘", "부산박물관", False),
    ("부산박물관의 잠금을 풀어주세요", "부산박물관", False),
])
def test_explicit_lock_requests(text, name, locked):
    action = extract_settings(text)
    assert action["action_type"] == "set_place_lock"
    assert action["target_name"] == name
    assert action["locked"] is locked


@pytest.mark.parametrize("text", ["Why is Busan Museum locked?", "Don't unlock Busan Museum", "부산박물관 잠금 해제하지마", "Replan unlocked activities"])
def test_questions_and_other_actions_do_not_edit_settings(text):
    assert extract_settings(text) is None


@pytest.mark.parametrize("amount", ["0 minutes", "1441 minutes", "0.25 minutes"])
def test_invalid_durations_are_not_silently_rounded(amount):
    assert extract_settings("Set the visit duration for Busan Museum to " + amount)["duration_min"] is None
