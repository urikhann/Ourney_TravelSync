# Mock services

Simple working stand-ins for the four teammate services, so the web app and backend work end to end from day one.
They follow `contracts/` exactly. Replace each one with the real service and change its URL in `.env`.

| Endpoint | Stands in for | What the mock does |
|---|---|---|
| `POST /recommend` | Recommender | Baseline: hard-constraint filter → weighted-average score per member → Average Without Misery → reasons. A good starting point for the recommender owner to beat. |
| `POST /plans` | Itinerary optimizer | Greedy: fills 10:00 · 12:30 · 14:00 · 16:00 · 18:30 slots, checking opening hours |
| `POST /availability/check` | Availability checker (postponed — not called in v1) | Weekly hours → public holidays / exceptions → start + duration + 15 min buffer before closing |
| `POST /agent/turn` | AI agent | Keyword stub (no LLM): replies with a fixed-format proposal |

Run alone: `pip install -r requirements.txt && DATABASE_URL=sqlite:///../../api/travelsync.db uvicorn main:app --port 8001`
