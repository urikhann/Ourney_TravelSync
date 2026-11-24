"""Transport, shared loop and paired-trial tests: no real API calls."""
from copy import deepcopy
import json

import httpx
import pytest

import brain
from providers import Completion, GROQ_MODELS, GroqProvider, ProviderError, ToolCall
import trial_models


@pytest.fixture(autouse=True)
def provider_environment(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-only-not-a-real-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)


@pytest.fixture
def fixture_data():
    return json.loads(trial_models.FIXTURE.read_bytes())


def wire_call(name, args, call_id="call-1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def response(calls):
    return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": calls}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}}


def provider_for(steps, model=GROQ_MODELS[0], seen=None):
    steps = iter(steps)
    def handle(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        step = next(steps)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            return httpx.Response(step, json={"error": "secret-provider-payload"})
        return httpx.Response(200, json=step)
    return GroqProvider(model, client=httpx.Client(transport=httpx.MockTransport(handle)))


def swap(poi="museum", start="10:00"):
    return {"date": "2026-11-21", "remove_item_ids": [f"2026-11-21@{start}"],
            "add": [{"poi_id": poi, "start": start}]}


@pytest.mark.parametrize("model", GROQ_MODELS)
@pytest.mark.parametrize("language", ["en", "ko"])
def test_groq_tool_roundtrip(fixture_data, model, language):
    case = fixture_data["cases"][1]
    seen, metrics = [], {}
    reply = "Try the museum." if language == "en" else "박물관으로 바꿔 볼까요?"
    provider = provider_for([
        response([wire_call("find_places", {"indoor": True}, "find")]),
        response([wire_call("check_hours", {"poi_id": "museum", "day": "2026-11-21", "start": "10:00"}, "hours")]),
        response([wire_call("answer", {"reply": reply, "proposal": swap()})]),
    ], model, seen)
    out = brain.model_turn(trial_models.trial_request(case, language), trial_models.TrialData(fixture_data, case),
                           provider, metrics=metrics)
    assert out["reply"] == reply and out["proposal"] == swap()
    assert seen[0]["model"] == model and seen[0]["tool_choice"] == "required"
    assert seen[0]["parallel_tool_calls"] is False
    assert "synthetic-member" not in seen[0]["messages"][1]["content"]
    assert "Member 1 asks:" in seen[0]["messages"][1]["content"]
    tool_result = seen[1]["messages"][-1]
    assert tool_result["role"] == "tool" and tool_result["tool_call_id"] == "find"
    assert json.loads(tool_result["content"])["result"]["places"][0]["poi_id"] == "museum"
    assert metrics["model_requests"] == 3 and metrics["usage"]["total_tokens"] == 75
    assert metrics["completed_answer"] and not metrics["proposal_rejected"]


@pytest.mark.parametrize("proposal,case_id", [(swap(start="14:00"), "rain"), (swap("invented"), "rain"), (swap(), "closed")])
def test_groq_uses_existing_proposal_validation(fixture_data, proposal, case_id):
    case = next(c for c in fixture_data["cases"] if c["id"] == case_id)
    metrics = {}
    out = brain.model_turn(trial_models.trial_request(case, "en"), trial_models.TrialData(fixture_data, case),
                           provider_for([response([wire_call("answer", {"reply": "Try this.", "proposal": proposal})])]),
                           metrics=metrics)
    assert out["proposal"] is None and metrics["proposal_rejected"]


@pytest.mark.parametrize("call", [
    wire_call("execute_sql", {"sql": "DELETE FROM pois"}),
    wire_call("find_places", {"limit": "wrong type"}),
    wire_call("find_places", {"max_cost_krw": 5000}),
    wire_call("answer", {"reply": "ok", "proposal": {"add": [{}]}}),
    wire_call("answer", {"reply": 123}), wire_call("answer", {"reply": " "}),
    wire_call("answer", {"reply": "ok", "proposal": swap() | {"add": [{"poi_id": "museum", "start": "11:00"}]}}),
])
def test_invalid_calls_never_execute_tools(fixture_data, monkeypatch, call):
    def forbidden(*args):
        pytest.fail("Invalid arguments must be rejected before tool execution")
    monkeypatch.setattr(brain, "run_tool", forbidden)
    with pytest.raises(ProviderError):
        brain.model_turn(trial_models.trial_request(fixture_data["cases"][0], "en"),
                         trial_models.TrialData(fixture_data, {}), provider_for([response([call])]))


@pytest.mark.parametrize("body", [
    {}, {"choices": []},
    {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}, response([]),
    response([wire_call("answer", {}) | {"function": {"name": "answer", "arguments": "bad-json"}}]),
    response([wire_call("answer", {"reply": "ok"}), wire_call("get_plan", {}, "other")]),
])
def test_malformed_or_mixed_completion_is_failure(fixture_data, body):
    with pytest.raises(ProviderError):
        brain.model_turn(trial_models.trial_request(fixture_data["cases"][0], "en"),
                         trial_models.TrialData(fixture_data, {}), provider_for([body]))


@pytest.mark.parametrize("failure", [401, 404, 429, 503, httpx.ReadTimeout("secret-provider-payload")])
def test_provider_failure_falls_back_safely(fixture_data, monkeypatch, failure):
    monkeypatch.setattr(brain, "TripData", lambda _: trial_models.TrialData(fixture_data, {}))
    provider = provider_for([failure])
    out = brain.turn(trial_models.trial_request(fixture_data["cases"][1], "en"), client=provider.client)
    assert out["tool_calls"][0]["name"] == "provider_error"
    assert "rules" in [c["name"] for c in out["tool_calls"]]
    assert "secret-provider-payload" not in json.dumps(out)


def test_step_limit_and_deadline(fixture_data):
    req = trial_models.trial_request(fixture_data["cases"][0], "en")
    data = trial_models.TrialData(fixture_data, {})
    with pytest.raises(ProviderError):
        brain.model_turn(req, data, provider_for([response([wire_call("get_plan", {})])] * brain.MAX_STEPS))
    with pytest.raises(TimeoutError):
        brain.model_turn(req, data, provider_for([]), started=brain.time.monotonic() - 41)


def test_settings_missing_key_and_explicit_rules(monkeypatch):
    import main
    assert main.health()["brain"] == "groq"
    monkeypatch.delenv("GROQ_API_KEY")
    assert main.health()["brain"] == "rules"
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    assert main.health()["brain"] == "gemini"
    monkeypatch.setenv("AGENT_PROVIDER", "rules")
    assert brain.provider_settings()["enabled"] is False
    monkeypatch.setenv("AGENT_PROVIDER", "typo")
    assert main.health()["ok"] is False


def test_groq_http_route(fixture_data, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.delenv("AGENT_PROVIDER")
    provider = provider_for([response([wire_call("answer", {"reply": "안녕하세요!", "proposal": None})])])
    monkeypatch.setattr(brain, "TripData", lambda _: trial_models.TrialData(fixture_data, {}))
    monkeypatch.setattr(brain, "GroqProvider", lambda *args, **kwargs: provider)
    client = TestClient(main.app)
    assert client.get("/health").json()["model"] == GROQ_MODELS[1]
    result = client.post("/agent/turn", json=trial_models.trial_request(fixture_data["cases"][0], "ko"))
    assert result.status_code == 200
    assert result.json() == {"reply": "안녕하세요!", "tool_calls": [], "proposal": None, "needs_clarification": False}


@pytest.mark.parametrize("failures", [0, 2, 6])
def test_groq_retries_before_gemini_backup(fixture_data, monkeypatch, failures):
    import providers

    seen = []
    steps = [429] * failures + [response([wire_call("answer", {"reply": "Groq answered"})])]
    transport = provider_for(steps, seen=seen).client
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    monkeypatch.setattr(brain, "TripData", lambda _: trial_models.TrialData(fixture_data, {}))
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    monkeypatch.setattr(brain, "gemini_with_fallback", lambda *args, **kwargs: {
        "reply": "Gemini answered", "tool_calls": [],
    })
    out = brain.turn(trial_models.trial_request(fixture_data["cases"][0], "en"), client=transport)
    assert len(seen) == min(failures + 1, 6)
    assert all(request["model"] == GROQ_MODELS[1] for request in seen)
    assert out["reply"] == ("Gemini answered" if failures == 6 else "Groq answered")


def test_paired_runner_no_database_or_fallback(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Trials must not use production DB, availability API, or rules fallback")
    import tools
    monkeypatch.setattr(tools.engine, "connect", forbidden)
    monkeypatch.setattr(tools.httpx, "post", forbidden)
    monkeypatch.setattr(brain, "rules_turn", forbidden)
    seen = []
    class FakeProvider:
        def __init__(self, model): self.model = model
        def start(self, system, prompt, tools): seen.append((self.model, system, prompt, deepcopy(tools)))
        def complete(self, timeout):
            if self.model == GROQ_MODELS[1]:
                raise ProviderError(429)
            return Completion([ToolCall("end", "answer", {"reply": "Hello!"})])
        def add_results(self, results): pass
    report = trial_models.build_report(trial_models.FIXTURE.read_bytes(), live=True, pause_seconds=0,
                                       provider_factory=FakeProvider)
    assert len(report["records"]) == 32
    for i in range(0, len(seen), 2):
        assert seen[i][0] != seen[i+1][0] and seen[i][1:] == seen[i+1][1:]
    assert report["summary"][GROQ_MODELS[0]]["answered"] == 16
    assert report["summary"][GROQ_MODELS[1]]["failed"] == 16
    assert report["summary"][GROQ_MODELS[1]]["answered"] == 0
    assert report["summary"][GROQ_MODELS[0]]["checks_all_true"] < 16


def test_dry_run_cli_and_no_overwrite(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    path = tmp_path / "report.json"
    assert trial_models.main(["--output", str(path)]) == 0
    original = path.read_bytes()
    report = json.loads(original)
    assert report["mode"] == "dry_run" and all(r["status"] == "not_run" for r in report["records"])
    with pytest.raises(SystemExit):
        trial_models.main(["--output", str(path)])
    assert path.read_bytes() == original
    with pytest.raises(SystemExit):
        trial_models.main(["--live", "--output", str(tmp_path / "live.json")])
    assert not (tmp_path / "live.json").exists()


def test_trial_retries_keep_failed_attempts_and_stop_on_answer(monkeypatch):
    monkeypatch.setattr(trial_models.time, "sleep", lambda _: None)
    attempts = iter([
        {"status": "failed", "http_status": 429},
        {"status": "answered", "response": {"proposal": {}}},
    ])
    monkeypatch.setattr(trial_models, "run_turn", lambda *args: next(attempts))
    report = trial_models.build_report(
        trial_models.FIXTURE.read_bytes(), live=True, models=(GROQ_MODELS[0],),
        case_id="rain", languages=("en",), retries=5,
    )
    row, = report["records"]
    assert row["status"] == "answered"
    assert [attempt.get("http_status") for attempt in row["attempts"]] == [429, None]
