# Run TravelSync with Docker

One command starts PostgreSQL, the recommender, the planning-data adapter,
the OR-Tools optimizer, availability mocks, the chat agent, the API, and the web app.
You only need **Docker Desktop** (Mac / Windows) — no Python, Node or uv on your computer.

```bash
docker compose up --build
```

Then open **http://localhost:5173**. The first start takes a few minutes (it builds the images and imports
3,570 places and their opening hours into PostgreSQL). Existing images and data are reused on later starts.

| Service | Port | What it is |
|---|---|---|
| web | 5173 | the web app (Vite dev server, reloads when you edit `web/src`) |
| api | 8000 | backend (reloads when you edit `api/app`) — API docs at http://localhost:8000/docs |
| recommender | 8002 | group recommendations (GFAR / AWM) — http://localhost:8002/health |
| optimizer | 8003 (loopback) | real CP-SAT Module H with E2/E3; called internally by the API |
| planning-data | 8004 (loopback) | operating facts and directed Kakao driving routes |
| agent | 8005 (loopback) | Gemini tools and rules fallback |
| mocks | 8001 | availability checker and test fixtures |
| db | 5432 | PostgreSQL (user / password / database: `travelsync`) |

Compose sets `PLANNING_DATA_URL=http://planning-data:8004`. Configure
`KAKAO_REST_API_KEY` for uncached driving routes, or set a trusted alternative
service implementing `POST /snapshot`. Optional authentication uses
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

### Slow rebuilds or growing Docker storage

The recommender image uses CPU-only PyTorch for E5. Python dependencies are
installed before application source in the recommender, optimizer and API images,
so source/data edits reuse the dependency layers. Package dependencies are read from `pyproject.toml` alongside service requirements.

Rebuild the Python services from the repository root:

```bash
docker compose --parallel 1 --progress plain build recommender optimizer api
docker compose up -d
```

This first rebuild still downloads dependencies. Later source-only rebuilds should
show `CACHED` for the dependency installation steps. Avoid `--no-cache` for normal
rebuilds. Single-service builds, such as `docker compose build recommender`, also
avoid rebuilding unrelated services. Starting existing images only needs
`docker compose up -d`.

To measure usage without deleting anything:

```bash
docker system df
docker buildx du
```

The Docker WSL disk file can remain large even after space is freed internally. Review the usage
before choosing cleanup. `docker compose down -v` deletes the PostgreSQL volume;
it is not a build performance fix. The web service also runs `npm install` on
startup; that time is separate from image building.

- **"port is already allocated"**: something else uses that port (for example another local server or PostgreSQL on 5432). Stop it, then run the command again.
- **The web page loads but nothing works**: wait until the api log says `Application startup complete`
  (the first start imports the place data).
- **After pulling new code something looks old**: `docker compose up --build`.
