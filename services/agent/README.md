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
cd ~/dev/Ourney_TravelSync/services/agent
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
