"""Model transports. Tool execution and proposal validation stay in brain.py.

Adapters own provider-specific history; neither adapter can execute a tool.
"""
from dataclasses import dataclass
import json
import os
import time
from typing import Protocol

import httpx


GROQ_MODELS = ("openai/gpt-oss-20b", "qwen/qwen3.8-27b")
DEFAULT_GROQ_MODEL = GROQ_MODELS[1]


class ProviderError(Exception):
    """Intentionally excludes raw response bodies, prompts and credentials."""

    def __init__(self, code: int | None = None, retry_after: float = 0):
        super().__init__("Model provider request failed")
        self.code = code
        self.retry_after = retry_after


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Completion:
    calls: list[ToolCall]
    text: str = ""
    usage: dict | None = None


class ModelProvider(Protocol):
    model: str

    def start(self, system: str, prompt: str, tools: list[dict]) -> None: ...
    def complete(self, timeout: float) -> Completion: ...
    def add_results(self, results: list[tuple[ToolCall, dict]]) -> None: ...


class GeminiProvider:
    """Retains the existing Gemini SDK configuration and function-result format."""

    def __init__(self, model: str, client=None, think: bool = True):
        from google import genai
        from google.genai import types

        self.model, self.think = model, think
        self.client = client if client is not None else genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=20_000,
                                          retry_options=types.HttpRetryOptions(attempts=1)))

    def start(self, system, prompt, tools):
        from google.genai import types

        thinking = (types.ThinkingConfig(thinking_budget=0) if self.model.startswith("gemini-2.5")
                    else types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW))
        self.config = types.GenerateContentConfig(
            system_instruction=system, temperature=0.2, max_output_tokens=8192,
            thinking_config=thinking if self.think else None,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in tools])],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        self.contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]

    def complete(self, timeout):
        from google.genai import types
        if timeout <= 0:
            raise TimeoutError("Model deadline exceeded")
        # Respect the shared remaining turn budget, including time already spent
        # in primary-provider retries and read tools. Never increase the 20s cap.
        bounded = self.config.model_copy(update={"http_options": types.HttpOptions(
            timeout=max(1, min(20_000, int(timeout*1000))),
            retry_options=types.HttpRetryOptions(attempts=1))})
        resp = self.client.models.generate_content(model=self.model, contents=self.contents, config=bounded)
        if any(getattr(candidate.finish_reason, "value", candidate.finish_reason) == "MAX_TOKENS"
               for candidate in (getattr(resp, "candidates", None) or [])):
            raise ProviderError()  # Never show a partial text or tool-call reply.
        calls = [ToolCall(str(i), fc.name, dict(fc.args or {})) for i, fc in enumerate(resp.function_calls or [])]
        if calls:
            self.contents.append(resp.candidates[0].content)
        metadata = getattr(resp, "usage_metadata", None)
        usage = {}
        if metadata is not None:
            for key, field in (
                ("prompt_tokens", "prompt_token_count"),
                ("completion_tokens", "candidates_token_count"),
                ("reasoning_tokens", "thoughts_token_count"),
                ("cached_prompt_tokens", "cached_content_token_count"),
                ("total_tokens", "total_token_count"),
            ):
                value = getattr(metadata, field, None)
                if isinstance(value, int):
                    usage[key] = value
        return Completion(calls, "" if calls else (resp.text or ""), usage)

    def add_results(self, results):
        from google.genai import types

        self.contents.append(types.Content(role="user", parts=[
            types.Part.from_function_response(name=call.name, response={"result": result})
            for call, result in results]))


class GroqProvider:
    """Groq Chat Completions over the service's existing HTTP dependency.

    One model per turn. Retries are opt-in and share a turn-level allowance.
    Trials leave transport retries disabled so each attempt remains observable.
    """

    def __init__(self, model: str, api_key: str | None = None, client=None, retries: int = 0):
        self.model = model
        self.api_key = api_key if api_key is not None else os.getenv("GROQ_API_KEY", "")
        self.client = client
        self.retries_left = retries

    def start(self, system, prompt, tools):
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        self.tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters_json_schema"]}}
            for t in tools]

    def complete(self, timeout):
        deadline = time.monotonic() + timeout
        delay = 0.5
        while True:
            try:
                return self._complete(max(0.001, deadline - time.monotonic()))
            except (ProviderError, TimeoutError) as error:
                code = getattr(error, "code", None)
                transient = code is None or code in {408, 429} or 500 <= code < 600
                wait = max(delay, getattr(error, "retry_after", 0))
                if not transient or not self.retries_left or time.monotonic() + wait >= deadline:
                    raise
                self.retries_left -= 1
                time.sleep(wait)
                delay = min(delay * 2, 4)

    def _complete(self, timeout):
        if not self.api_key:
            raise ProviderError(401)
        payload = {"model": self.model, "messages": self.messages, "tools": self.tools,
                   "tool_choice": "required", "parallel_tool_calls": False,
                   "temperature": 0.2, "max_completion_tokens": 8192, "stream": False,
                   "reasoning_effort": "low"}
        post = self.client.post if self.client is not None else httpx.post
        try:
            response = post("https://api.groq.com/openai/v1/chat/completions",
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            json=payload, timeout=min(20.0, timeout))
            if response.status_code >= 400:
                retry_after = response.headers.get("Retry-After", "")
                raise ProviderError(response.status_code, int(retry_after) if retry_after.isdecimal() else 0)
            body = response.json()
            choice = body["choices"][0]
            if choice.get("finish_reason") != "tool_calls":
                raise ProviderError()
            message = choice["message"]
            raw_calls = message.get("tool_calls") or []
            calls = []
            for raw in raw_calls:
                args = json.loads(raw["function"]["arguments"])
                if (raw.get("type") != "function" or not isinstance(args, dict)
                        or not isinstance(raw.get("id"), str) or not raw["id"]):
                    raise ProviderError()
                calls.append(ToolCall(raw["id"], raw["function"]["name"], args))
            if not calls or len({c.id for c in calls}) != len(calls):
                raise ProviderError()
            # Keep tool-call history, never expose or persist provider reasoning.
            self.messages.append({"role": "assistant", "content": None, "tool_calls": raw_calls})
            usage = {k: v for k, v in (body.get("usage") or {}).items()
                     if k in ("prompt_tokens", "completion_tokens", "total_tokens") and isinstance(v, int)}
            return Completion(calls, usage=usage)
        except httpx.TimeoutException:
            raise TimeoutError("Model provider timed out") from None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError() from None

    def add_results(self, results):
        self.messages.extend({"role": "tool", "tool_call_id": call.id, "name": call.name,
                              "content": json.dumps({"result": result}, ensure_ascii=False)}
                             for call, result in results)
