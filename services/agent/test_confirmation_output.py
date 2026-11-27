"""Structured confirmation metadata and deterministic final prose fallback."""
import brain
import pytest
import trial_models
from test_providers import fixture_data, provider_environment, provider_for, response, wire_call, swap


@pytest.mark.parametrize("language,text,expected", [
    ("ko", "@TravelSync 2026-11-21", True),
    ("en", "@TravelSync 안녕하세요", False),
    (None, "@TravelSync 안녕하세요", True),
])
def test_reply_language_overrides_text_detection(language, text, expected):
    assert brain.reply_korean(dict(reply_language=language, message={"text": text})) is expected


def test_selected_language_is_in_model_system_instructions(fixture_data):
    case = fixture_data["cases"][0]
    request = trial_models.trial_request(case, "en")
    request["reply_language"] = "ko"
    seen = []
    provider = provider_for([response([wire_call("answer", dict(reply="확인했어요."))])], seen=seen)
    brain.model_turn(request, trial_models.TrialData(fixture_data, case), provider)
    assert "Selected reply language: Korean" in seen[0]["messages"][0]["content"]


def test_gemini_request_respects_remaining_budget():
    from providers import GeminiProvider
    from google.genai import types
    from types import SimpleNamespace
    seen = []
    class Models:
        def generate_content(self, **kwargs):
            seen.append(kwargs["config"].http_options.timeout)
            return SimpleNamespace(function_calls=[], text="", usage_metadata=None)
    provider = GeminiProvider("synthetic-model", client=SimpleNamespace(models=Models()))
    provider.config = types.GenerateContentConfig()
    provider.contents = []
    provider.complete(1.25)
    provider.complete(60)
    provider.complete(120)
    assert seen == [1250, 60000, 60000]


def test_gemini_token_truncated_text_is_not_displayed():
    from providers import GeminiProvider, ProviderError
    from google.genai import types
    from types import SimpleNamespace
    class Models:
        def generate_content(self, **kwargs):
            return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=types.FinishReason.MAX_TOKENS)],
                                   function_calls=[], text="Incomplete **Group", usage_metadata=None)
    provider = GeminiProvider("synthetic-model", client=SimpleNamespace(models=Models()))
    provider.config = types.GenerateContentConfig()
    provider.contents = []
    with pytest.raises(ProviderError):
        provider.complete(5)


def test_confirmation_question_has_structured_operation(fixture_data):
    case = next(c for c in fixture_data["cases"] if c["id"] == "rain")
    action = dict(action_type="replace_poi", target_item_id=swap()["remove_item_ids"][0], constraints={})
    out = brain.model_turn(trial_models.trial_request(case, "en"), trial_models.TrialData(fixture_data, case),
        provider_for([response([wire_call("answer", dict(reply="Shall I search for a replacement?", pending_action=action))])]))
    assert out["proposal"] is None and out["needs_clarification"]
    assert out["pending_action"] == action


def test_complete_suggestion_survives_missing_final_model_prose(fixture_data):
    case = next(c for c in fixture_data["cases"] if c["id"] == "rain")
    out = brain.model_turn(trial_models.trial_request(case, "en"), trial_models.TrialData(fixture_data, case),
        provider_for([response([wire_call("answer", dict(reply="", proposal=swap()))])]))
    assert out["proposal"] == swap()
    assert "backend" in out["reply"] and "before voting" in out["reply"]
