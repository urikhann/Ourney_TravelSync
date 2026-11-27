import pytest
from app.planning_intents import extract, confirmation


@pytest.mark.parametrize("text,scope", [
    ("@TravelSync replan from scratch again.", "replan_all"),
    ("@TravelSync 전체 일정을 다시 짜줘", "replan_all"),
    ("replan unlocked activities", "replan_unlocked"),
    ("잠기지 않은 일정을 다시 계획해줘", "replan_unlocked"),
    ("replan day 2026-11-30", "replan_day"),
    ("2026-11-30 일정을 다시 짜줘", "replan_day"),
    ("replace Concert Hall with a cafe", "replace_poi"),
    ("콘서트홀을 카페로 바꿔줘", "replace_poi"),
    ("remove Concert Hall", "remove_poi"),
    ("콘서트홀을 빼줘", "remove_poi"),
    ("add Cafe Z on 2026-11-30", "add_poi"),
    ("카페 지를 추가해줘", "add_poi"),
])
def test_explicit_scope_is_preserved(text, scope):
    assert extract(text)["action_type"] == scope


def test_new_unsupported_hard_condition_is_not_discarded():
    assert extract("replan all avoiding museums")["constraints"]["needs_clarification"]
    assert extract("replan all exclude museums")["constraints"]["needs_clarification"]
    assert extract("why was the plan changed?") is None
    assert confirmation("@ai Yes.") is True
    assert confirmation("Yes, please.") is True
    assert confirmation("네, 진행해주세요.") is True
    assert confirmation("아니요") is False
    assert confirmation("Yes, but change the date") is None


@pytest.mark.parametrize("text,preference", [
    ("It's raining — can we swap outdoor stops for indoor places?", "indoor"),
    ("It's raining — please replan today's whole day to minimize outdoor activities.", "indoor"),
    ("We're tired — please make today's plan need less walking.", "less_walking"),
    ("We're tired — please replan today's whole day to minimize walking.", "less_walking"),
    ("비가 와요 — 오늘 하루 전체 일정을 야외 활동이 최소가 되도록 다시 계획해 주세요.", "indoor"),
    ("너무 피곤해요 — 오늘 하루 전체 일정을 걷기가 최소가 되도록 다시 계획해 주세요.", "less_walking"),
])
def test_comfort_shortcuts_request_whole_day(text, preference):
    from app.planning_intents import unsupported_constraints
    action = extract("@TravelSync " + text)
    assert action == dict(action_type="replan_day", date="today", constraints={"day_preference": preference})
    assert not unsupported_constraints(action)
