import pytest

from app import conversation, planning_actions, services
from app.routes.chat import ChatIn
from pydantic import ValidationError
from .test_conversation_actions import post
from .test_proposal_approval import planned_trip, replacement_fixture_clock


@pytest.mark.parametrize("override,expected", [(None, "ko"), ("ko", "ko"), ("en", "en")])
def test_date_choice_keeps_korean_or_explicit_ui_language(planning_adapter, replacement_fixture_clock, monkeypatch, override, expected):
    (owner,), tid, _ = planned_trip(1)
    question = post(owner, tid, "@TravelSync 너무 피곤해요 — 오늘 일정은 덜 걷게 바꿔 주세요.")
    assert question["agent"]["language"] == "ko"
    assert "선택" in question["agent"]["reply_localized"]["ko"]

    async def execute(db, trip, message, plan, audit, action):
        assert action["constraints"] == {"day_preference": "less_walking"}
        assert action["date"] == "2026-11-06"
        return planning_actions.failure("no_unlocked_activities", conversation.korean(message))

    monkeypatch.setattr(planning_actions, "execute", execute)
    extra = {} if override is None else {"language": override}
    answer = post(owner, tid, "@TravelSync 2026-11-06", reply_to=question["id"], trigger="QUICK_ACTION", **extra)
    agent = answer["agent"]
    assert agent["language"] == expected
    assert agent["reply"] == agent["reply_localized"][expected]
    assert "보호" in agent["reply_localized"]["ko"]


def test_selected_korean_ui_language_reaches_agent_service(planning_adapter, monkeypatch):
    (owner,), tid, _ = planned_trip(1)
    seen = []

    async def agent_turn(payload):
        seen.append(payload)
        return dict(reply="도와드릴게요.", proposal=None, tool_calls=[])

    monkeypatch.setattr(services, "agent_turn", agent_turn)
    answer = post(owner, tid, "@TravelSync Hello", language="ko")
    assert seen[0]["reply_language"] == "ko"
    assert answer["text"] == "도와드릴게요."


def test_chat_reply_language_only_accepts_supported_locales():
    with pytest.raises(ValidationError):
        ChatIn(text="@TravelSync Hello", language="fr")


def test_bilingual_failures_preserve_validation_context():
    answer = planning_actions.failure("final_validation_failed", True, details=["day:CONSECUTIVE_FOOD_DRINK"])
    assert answer["reply"] == answer["reply_localized"]["ko"]
    assert "음식·음료" in answer["reply_localized"]["ko"]
    assert "food/drink" in answer["reply_localized"]["en"]
