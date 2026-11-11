# Run TravelSync with Docker

One command starts PostgreSQL, the real recommender, the OR-Tools optimizer,
the remaining mock services, the backend and the web app.
You only need **Docker Desktop** (Mac / Windows) — no Python, Node or uv on your computer.

```bash
docker compose up --build
```

Then open **http://localhost:5173**. The first start takes a few minutes (it builds the images and imports
3,570 places and their opening hours into PostgreSQL). Later starts take seconds.

| Service | Port | What it is |
|---|---|---|
| web | 5173 | the web app (Vite dev server, reloads when you edit `web/src`) |
| api | 8000 | backend (reloads when you edit `api/app`) — API docs at http://localhost:8000/docs |
| recommender | 8002 | the team package (GFAR / AWM) — http://localhost:8002/health |
| optimizer | 8003 (loopback) | real CP-SAT Module H with E2/E3; called internally by the API |
| mocks | 8001 | availability checker and AI agent stand-ins; legacy mock planner is not used by the app |
| db | 5432 | PostgreSQL (user / password / database: `travelsync`) |

Itinerary generation requires `PLANNING_DATA_URL` to point to your trusted
operating-data/routing service (`POST /snapshot`). Optional authentication uses
`PLANNING_DATA_TOKEN`; keep real credentials in the ignored `.env` or exported
environment. Without a configured source, the app explains that verified planning
data is missing and preserves existing plans. It never substitutes synthetic or
zero-time/zero-cost routes. See [adapter setup and contract](docs/optimizer_app_integration.md).
API startup adds the private `itinerary_optimization_runs` audit table; existing
tables and columns are unchanged.

## Everyday commands

| I want to… | Command |
|---|---|
| stop | `Ctrl + C`, or `docker compose down` |
| start again | `docker compose up` (add `--build` after pulling new code) |
| use the 40 demo places | `PLACES_FILE=travelsync_demo40.csv PLACES_VERSION=busan-demo40-v1 docker compose up` |
| go back to all 3,570 places | `docker compose up` |
| see the email verification code | it is printed in the log: `docker compose logs api \| grep -A3 EMAIL` |
| open the database | `docker compose exec db psql -U travelsync` (`\dt` lists tables, `\q` quits) |
| delete all data and start fresh | `docker compose down -v` |

On Windows PowerShell, set the demo variables first:
`$env:PLACES_FILE="travelsync_demo40.csv"; $env:PLACES_VERSION="busan-demo40-v1"; docker compose up`

## Settings

Defaults work without any file. To change them, copy `.env.example` to `.env`
(for example `SMTP_*` to send real emails, or `FEATURE_AVAILABILITY=false` to hide opening-hours checks).
The service addresses inside Docker are fixed in `docker-compose.yml`.

## Troubleshooting

- **"port is already allocated"**: something else uses that port (often a local server from the 4-terminal setup,
  or a local PostgreSQL on 5432). Stop it, then run the command again.
- **The web page loads but nothing works**: wait until the api log says `Application startup complete`
  (the first start imports the place data).
- **After pulling new code something looks old**: `docker compose up --build`.
