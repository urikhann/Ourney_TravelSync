# TravelSync agent service (port 8005)

The AI behind `@TravelSync` in the trip chat. Same contract as the mock (`contracts/agent.*.json`),
so the backend only needs `AGENT_URL=http://localhost:8005`.

| Piece | File | What it does |
|---|---|---|
| HTTP | `main.py` | `POST /agent/turn`, `GET /health` (selected provider, active brain and model) |
| Brain | `brain.py` | Shared tool loop (max 6 steps, ends with `answer`); rules fallback |
| Providers | `providers.py` | Gemini SDK adapter and Groq Chat Completions adapter |
| Tools | `tools.py` | read-only: `get_plan`, `find_places`, `check_hours`; `validate` checks every suggestion |

Safety: tools only read. A suggestion is dropped unless the stop exists, is not locked, the place is real and
not in the plan, and it is not closed at that time. Member identity fields are replaced by "Member 1, 2…".
User-written chat text and place facts still go to the selected provider. Without its API key, or when the
provider fails, simple rules answer instead. This does not establish full itinerary feasibility.

## Select a provider

`AGENT_PROVIDER` accepts `gemini` (default), `groq`, or `rules`. `GEMINI_API_KEY` and `GEMINI_MODEL`
keep their existing behavior. Groq uses `GROQ_API_KEY` and `GROQ_MODEL` (default `openai/gpt-oss-20b`);
the paired Qwen candidate is `qwen/qwen3.8-27b`. Model availability depends on the Groq account.
Keys belong only in the ignored root `.env`, exported environment, or deployment secret configuration.

For Docker, set these entries in the root `.env`, then rebuild the agent:

```dotenv
AGENT_PROVIDER=groq
GROQ_MODEL=openai/gpt-oss-20b
```

Supply `GROQ_API_KEY` separately. Change `GROQ_MODEL` to `qwen/qwen3.8-27b` to trial Qwen in the app.

```powershell
docker compose up -d --build agent
```

`GET /health` describes configuration, not a live provider probe. With the selected key missing,
`brain` is `rules`; it never silently switches to a different provider. An invalid provider reports
`ok: false`. Model failures produce sanitized `provider_error` tool summaries; Gemini retains its
legacy `gemini_error` name. Model responses never execute arbitrary functions: the shared loop
dispatches only the allowlisted read tools and validates suggestions through `TripData.validate`.
Groq additionally validates argument schemas and requires one complete swap at the original start time.

## Paired Korean/English trials

Run from the repository root, using an environment installed from `services/agent/requirements.txt`:

```powershell
python services/agent/trial_models.py --output output/agent-trials/dry-run.json
# With GROQ_API_KEY exported in this shell:
python services/agent/trial_models.py --live --output output/agent-trials/live-01.json
```

The default dry run lists 32 planned turns, all marked `not_run`: eight synthetic scenarios in both
languages, each sent to both candidates. `--live` calls Groq directly; it never writes production chat,
reads the app database, calls the availability service, or substitutes rules/Gemini responses. It uses
the production tool and validation methods over in-memory synthetic places and fixed hours.
The cases cover greetings, rain, walking, locked stops, unknown/closed hours, ambiguity, and no plan.

Each model receives the same system prompt, tool schemas, case context, temperature 0.2, low reasoning
effort, 2,048 completion-token cap, six-step bound, and 40-second loop budget. HTTP calls use at most
20-second timeouts; blocking I/O and final validation can extend elapsed time beyond the loop budget.
The first model alternates across cases/languages/repeats. `--repeats 3` repeats the paired set;
`--models MODEL_A MODEL_B` overrides the two candidate IDs if availability changes.

Reports record source/fixture/prompt/schema hashes, settings, individual replies/proposals, model
request counts, returned token usage, elapsed time, rejected proposals, and sanitized API failures.
Existing report files are never overwritten. Outputs under `output/` are ignored by Git.
There is no cross-model retry: failed requests remain failures. The runner pauses 10 seconds between
turns (`--pause-seconds 0..60`); multi-step turns can still hit token limits. A live run exits nonzero
if any model turn fails, after saving the report.

The automatic checks cover proposal presence, clarification on the ambiguous case, rejection by the
existing validator, and a Hangul-based language heuristic. Review Korean/English quality, unsupported
claims, and unknown-hours wording manually before choosing a model. These synthetic checks are not
relevance/satisfaction labels or proof of real-world itinerary feasibility; no winner is assumed.

Provider references: [Groq tool calling](https://console.groq.com/docs/tool-use/local-tool-calling),
[reasoning parameters](https://console.groq.com/docs/reasoning),
[rate limits](https://console.groq.com/docs/rate-limits).

## Run locally

```bash
cd services/agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY=...   # free key from https://aistudio.google.com/apikey (leave out to use rules)
DATABASE_URL=sqlite:///../../api/travelsync.db AVAILABILITY_URL=http://localhost:8001 uvicorn main:app --port 8005
```

Then start the API with `AGENT_URL=http://localhost:8005`. Optional: `GEMINI_MODEL` (default `gemini-3.8-flash`).

## Test (no key or internet needed — fake Gemini and Groq transports)

```bash
python -m pytest -q
```


## Product scope and provider errors

Chat supports indoor alternatives and less walking. Budget optimization, cheaper-plan
suggestions and cost filtering are excluded. Agent tools omit price fields. Opening
hours checks keep the itinerary's pinned dataset; unknown hours remain unknown.

The default Gemini provider/model configuration is unchanged. Gemini failures keep the rules
fallback, but new tool summaries distinguish HTTP 404 (model unavailable to this
API account), 429 (quota/rate limit), and 503 (provider overload). Logs include the
attempted model and sanitized reason, without raw provider payloads or API keys.
Old chat messages keep their original saved summaries.

A direct provider diagnostic on 21 November 2026 confirmed the configured primary
model exists and the key can list models. The primary returned 503/high demand
and also timed out in subsequent checks; `gemini-3.5-flash-lite` answered the same
tool-calling probe, while `gemini-2.5-flash` returned HTTP 404 with the provider
stating it is unavailable to new users. This explains a concrete failing fallback
path, but the exact error behind an already-saved generic `ClientError` cannot be
recovered from that summary alone. Provider/model selection is deferred to a
separate branch. No local-model integration or model download was added.

Rebuild the agent to use the cleaned tools and error messages:

```powershell
docker compose up -d --build agent
```

Compose's development web server picks up source edits. A separately built frontend
needs a new build. Existing chat history and database records are not rewritten.
