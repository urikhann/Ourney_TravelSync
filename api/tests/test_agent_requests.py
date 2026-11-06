"""Request-format and explicit-trigger tests."""

import pytest
from pydantic import ValidationError

from app.agent.requests import AgentRequestInput


BASE_REQUEST = {
    "trigger": "QUICK_ACTION",
    "message": "What are our trip dates?",
    "idempotency_key": "request-001",
}


@pytest.mark.parametrize(
    "message",
    [
        "@TravelSync What are our trip dates?",
        "@TravelSync 우리 여행 날짜를 알려줘.",
        "   @travelsync Why was this place recommended?",
    ],
)
def test_explicit_mentions_are_accepted(message):
    request = AgentRequestInput(
        trigger="MENTION",
        message=message,
        idempotency_key="request-001",
    )

    assert request.trigger == "MENTION"
    assert request.message == message
    assert request.run_id is None
    assert request.poi_ids == []


@pytest.mark.parametrize(
    "message",
    [
        "What are our trip dates?",
        "이 장소가 추천된 이유를 알려줘.",
    ],
)
def test_quick_actions_do_not_require_mentions(message):
    request = AgentRequestInput(
        trigger="QUICK_ACTION",
        message=message,
        idempotency_key="request-002",
    )

    assert request.message == message
    assert request.trigger == "QUICK_ACTION"


@pytest.mark.parametrize(
    "overrides",
    [
        {"trigger": "MENTION"},
        {
            "trigger": "MENTION",
            "message": "Hello @TravelSync what are our dates?",
        },
        {
            "trigger": "MENTION",
            "message": "@TravelSyncBot what are our dates?",
        },
        {
            "trigger": "MENTION",
            "message": "@TravelSync",
        },
        {"message": "   "},
        {"message": "x" * 2001},
        {"trigger": "AUTO"},
        {"actor_id": "forged-user"},
        {"run_id": "12"},
        {"run_id": True},
        {"poi_ids": ["kakao:1", "kakao:1"]},
        {"poi_ids": [str(i) for i in range(21)]},
        {"idempotency_key": "contains spaces"},
        {"tool_name": "commit_itinerary"},
    ],
)
def test_invalid_requests_are_rejected(overrides):
    payload = {**BASE_REQUEST, **overrides}

    with pytest.raises(ValidationError):
        AgentRequestInput.model_validate(payload)
        
