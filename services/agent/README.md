# TravelSync agent service (port 8005)

The AI behind `@TravelSync` in the trip chat. Same contract as the mock (`contracts/agent.*.json`),
so the backend only needs `AGENT_URL=http://localhost:8005`.

| Piece | File | What it does |
|---|---|---|
| HTTP | `main.py` | `POST /agent/turn`, `GET /health` (shows `gemini` or `rules`) |
| Brain | `brain.py` | Gemini with function calling (max 6 steps, ends with `answer`); rules fallback |
| Tools | `tools.py` | read-only: `get_plan`, `find_places`, `check_hours`; `validate` checks every suggestion |

Safety: tools only read. A suggestion is dropped unless the stop exists, is not locked, the place is real and
not in the plan, and it is not closed at that time. Members are sent to Gemini as "Member 1, 2…", never names
or emails. Without `GEMINI_API_KEY`, or when Gemini fails (quota, network), simple rules answer instead.

## Run locally

```bash
cd services/agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY=...   # free key from https://aistudio.google.com/apikey (leave out to use rules)
DATABASE_URL=sqlite:///../../api/travelsync.db AVAILABILITY_URL=http://localhost:8001 uvicorn main:app --port 8005
```

Then start the API with `AGENT_URL=http://localhost:8005`. Optional: `GEMINI_MODEL` (default `gemini-3.8-flash`).

## Test (no key or internet needed — a fake Gemini replays tool calls)

```bash
python -m pytest -q
```


## Product scope and provider errors

Chat supports indoor alternatives and less walking. Budget optimization, cheaper-plan
suggestions and cost filtering are excluded. Agent tools omit price fields. Opening
hours checks keep the itinerary's pinned dataset; unknown hours remain unknown.

The provider/model configuration is unchanged. Gemini failures keep the rules
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
