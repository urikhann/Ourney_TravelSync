# Mock services

Fixtures implement recommendation, availability, planning, and agent contracts
for local tests. The app uses the separate recommender, optimizer, and agent
services. The greedy mock planner is not a production fallback.

| Endpoint | Behavior |
|---|---|
| `POST /recommend` | Weighted feature scores and configurable group aggregation |
| `POST /plans` | Greedy fixed-slot plan for mock-contract tests |
| `POST /availability/check` | Weekly/dated/normalized hours, holidays, and a 15-minute closing buffer |
| `POST /agent/turn` | Keyword-based reply and structured proposal |

From `services/mocks/`, install `requirements.txt`, configure `DATABASE_URL`,
and run `uvicorn main:app --port 8001`. Make the root package available with
an editable installation or `PYTHONPATH` pointing to `src/`.

See [planning service configuration](../../docs/optimizer_app_integration.md)
for the optimizer's separate typed contract.
