"""Replacement rules remain relevant after provider timeout; no live API calls."""
import pytest

import brain


class FixtureData:
    def get_plan(self):
        return {"plan": [{"date": "2026-11-30", "items": [{"item_id": "2026-11-30@19:08",
            "poi_id": "synthetic-hall", "name": "경성대학교 콘서트홀", "start": "19:08", "locked": False}]}]}

    def find_places(self, **kwargs):
        return {"places": [{"poi_id": "synthetic-cafe", "name": "Synthetic cafe", "duration_min": 30}]}

    def check_hours(self, *args):
        return {"available": True}

    def validate(self, proposal):
        return proposal, None


@pytest.mark.parametrize("text", ["경성대학교 콘서트홀 가기 싫어 다른 곳으로 치환해줘.",
                                  "경성대학교 콘서트홀 대신 다른 곳 추천해줘.",
                                  "경성대학교 콘서트홀 빼고 다른 장소 넣어줘.",
                                  "콘서트홀을 카페로 바꿔줘."])
def test_tc001_tc013_rules_replacement_after_model_timeout(monkeypatch, text):
    monkeypatch.setenv("AGENT_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(brain, "TripData", lambda _: FixtureData())
    def timeout(*a, **kw):
        raise TimeoutError("synthetic model timeout")
    monkeypatch.setattr(brain, "model_turn", timeout)
    result = brain.turn({"itinerary_id": 1, "message": {"text": text}})
    assert result["proposal"]["remove_item_ids"] == ["2026-11-30@19:08"]
    assert "비 오는" not in result["reply"]
    assert "timed out" in result["tool_calls"][0]["summary"]
