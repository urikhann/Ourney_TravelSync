# TravelSync agent service (port 8005)

The AI behind `@TravelSync` in the trip chat. Same contract as the mock (`contracts/agent.*.json`),
so the backend only needs `AGENT_URL=http://localhost:8005`.

| Piece | File | What it does |
|---|---|---|
| HTTP | `main.py` | `POST /agent/turn`, `GET /health` (selected provider, active brain and model) |
| Brain | `brain.py` | Shared tool loop (max 6 steps); answers or requests settings confirmation; rules fallback |
| Providers | `providers.py` | Gemini SDK adapter and Groq Chat Completions adapter |
| Tools | `tools.py` | read-only: `get_plan`, `find_places`, `check_hours`; `validate` checks every suggestion |

Safety: tools only read. A suggestion is dropped unless the stop exists, is not locked, the place is real and
not in the plan, and it is not closed at that time. Member identity fields are replaced by "Member 1, 2…".
User-written chat text and place facts go to the provider handling the turn.

## Visit duration and place locks

The agent also exposes `set_visit_duration(item_id, duration_min)` and
`set_place_lock(item_id, locked)`. Both use an existing scheduled `item_id` and
return a confirmation request; the agent service does not write to the database.
Only the trip owner can confirm these settings changes.

- A duration change keeps all existing places, validates the adjusted schedule,
  and creates a draft for the normal group vote. Other stops may move within the
  same day to fit; protected start times and unaffected dates remain fixed.
  An infeasible change leaves the current plan intact.
- A lock or unlock reuses the Plan screen's protection controls. Confirmation
  changes only lock metadata, preserving the schedule and existing plan votes.
- Confirmed reservations and visits that have started cannot be edited through
  either tool. Stale or already handled confirmations cannot execute again.

Examples: `@ai Set the visit duration for Busan Museum to 90 minutes`,
`@ai Lock Busan Museum`, `@ai 부산박물관 방문 시간을 90분으로 바꿔줘`, and
`@ai 부산박물관 잠금 해제해줘`. Ambiguous names require clarification.

## Select a provider

`AGENT_PROVIDER` accepts `groq` (default), `gemini`, or `rules`. Groq uses `GROQ_API_KEY` and
`GROQ_MODEL` (default `qwen/qwen3.8-27b`). The default path allows up to five Groq retries per turn
for retryable provider errors, reusing tool history and applying backoff. When a Gemini key is
configured, Groq gets up to 25 seconds of the 40-second model budget. Gemini handles the turn if
Groq is unavailable or cannot finish within its budget. Simple rules answer if both paths fail or
neither key is configured.
Keys belong only in the ignored root `.env`, exported environment, or deployment secret configuration.

For Docker, set these entries in the root `.env`, then rebuild the agent:

```dotenv
AGENT_PROVIDER=groq
GROQ_MODEL=qwen/qwen3.8-27b
```

Supply `GROQ_API_KEY` for Qwen and `GEMINI_API_KEY` to enable the Gemini backup.

```powershell
docker compose up -d --build agent
```

`GET /health` reports the configured primary and the brain available based on API keys.
An invalid provider reports `ok: false`. Model failures produce sanitized `provider_error` tool summaries if the turn goes to rules. Explicit Gemini mode retains its
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

Planning confirmations now persist in the API's chat JSON, scoped to the user,
question and itinerary version. Short affirmative/negative replies are handled
without another model call. Every confirmation question emitted by the model must
include `pending_action`; the backend creates and validates the trusted envelope.
See [conversation and re-planning](../../docs/conversation_state_and_replanning.md).
Gemini now applies the remaining turn budget to each SDK request, retaining the
20-second cap and one attempt. A candidate with missing final prose is handed to
backend validation using a deterministic sentence; it is not certified by the LLM.

Named replacement requests use the API's shared deterministic intent parser and
[pre-vote repair gate](../../docs/localized_itinerary_repair.md). Model/rule output is
only a suggestion until that backend gate stores an E3-validated draft. Approval
applies the exact stored schedule without another optimizer call. Unknown or provisional
replacement hours cannot qualify for voting. Ambiguous targets require clarification.
Build the agent image from repository-root context (Compose is already configured).

Chat supports indoor alternatives and less walking. Budget optimization, cheaper-plan
suggestions and cost filtering are excluded. Agent tools omit price fields. Opening
hours checks keep the itinerary's pinned dataset; unknown hours remain unknown.

Provider failures retain sanitized reason codes for model unavailability,
quota/rate limits, overload and timeouts. Raw provider payloads and keys are not
logged. Existing chat messages retain their saved summaries.

Rebuild the agent after source or provider configuration changes:

```powershell
docker compose up -d --build agent
```

Compose's development web server picks up source edits. A separately built frontend
needs a new build. Existing chat history and database records are not rewritten.
