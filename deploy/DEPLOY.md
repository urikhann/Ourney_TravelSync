# Deploy TravelSync on AWS Lightsail

One Lightsail server runs the whole Docker Compose stack.
Caddy (in the `web` container) serves the built web app on port 80/443 and forwards `/api/*` to the API.
Nothing else is open to the internet.

```
Browser ──80/443──▶ web (Caddy) ──/api/*──▶ api:8000 ──▶ db · recommender · optimizer · planning-data · agent · mocks
                      └─ everything else ─▶ built React app
```

## Files

| File | What it does |
|---|---|
| `web/Dockerfile.prod` | Builds the React app once (`VITE_API_URL=/api`) and packs it into a Caddy image |
| `web/.dockerignore` | Keeps `node_modules` / `dist` out of the build |
| `deploy/Caddyfile` | Serves the app, proxies `/api/*` to the API, HTTPS automatically when `SITE_ADDRESS` is a domain |
| `docker-compose.prod.yml` | Production overrides: web on 80/443, API without `--reload`, every other port bound to `127.0.0.1`, auto restart |

## 1. Create the server (Lightsail console)

| Setting | Value |
|---|---|
| Region | Seoul (`ap-northeast-2`, zone a) |
| Platform / blueprint | Linux/Unix → **OS Only → Ubuntu 24.04 LTS** |
| Plan | **8 GB RAM, 2 vCPU, 160 GB SSD** (about $44/month) |
| Name | `travelsync` |

Then:
1. **Networking → Create static IP** → attach it to `travelsync` (free while attached).
2. **Networking → IPv4 Firewall**: keep SSH 22 and HTTP 80, **add HTTPS 443**.
3. **Snapshots → Enable automatic snapshots** (daily backup).
4. Billing console → **Budgets → Create budget** (monthly cost budget, e.g. $50, email alert).

## 2. Connect and install Docker

Click **Connect using SSH** in the Lightsail console (browser terminal), then:

```
sudo apt-get update
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit
```

Connect again (so the docker group applies) and check:

```
docker compose version
```

## 3. Get the code

```
git clone https://github.com/urikhann/Ourney_TravelSync.git
cd Ourney_TravelSync
```

## 4. Create `.env`

```
cp .env.example .env
openssl rand -hex 32
openssl rand -hex 16
nano .env
```

Use the first random value for `SECRET_KEY` and the second for `POSTGRES_PASSWORD`. Set these lines:

| Key | Value |
|---|---|
| `COMPOSE_FILE` | `docker-compose.yml:docker-compose.prod.yml` (then plain `docker compose` uses both files) |
| `SITE_ADDRESS` | `:80` (no domain yet) or your domain, e.g. `travelsync.example.com` |
| `PUBLIC_URL` | `http://<STATIC_IP>` or `https://<your domain>` (invite links + CORS) |
| `SECRET_KEY` | first random value |
| `POSTGRES_PASSWORD` | second random value |
| `GEMINI_API_KEY`, `KAKAO_REST_API_KEY` | team keys (never commit them) |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.gmail.com` / `587` |
| `SMTP_USER` / `SMTP_PASSWORD` | team Gmail + a Gmail **app password** (Google account → Security → 2-Step Verification → App passwords) |
| `MAIL_FROM` | same Gmail address |

Save: `Ctrl+O`, `Enter`, `Ctrl+X`.
Without SMTP, sign-up codes only appear in `docker compose logs api`.

## 5. Build, load the V4 places, start

```
docker compose build
docker compose up -d db
docker compose run --rm --no-deps api python -m app.deployment --activate
docker compose run --rm --no-deps recommender python main.py --prewarm --download
docker compose up -d
docker compose ps
```

The first build and the E5 model download take 10–20 minutes. All services should become `healthy`.

## 6. Test

Open `http://<STATIC_IP>` on a phone: sign up → verification mail → create a trip → recommendations → plan → chat.

```
curl -s http://localhost/api/health
docker compose logs --tail 50 api
```

## 7. HTTPS (when we have a domain)

1. DNS: an `A` record for the domain → the static IP.
2. In `.env`: `SITE_ADDRESS=travelsync.example.com` and `PUBLIC_URL=https://travelsync.example.com`.
3. `docker compose up -d` → Caddy gets a free Let's Encrypt certificate by itself.

HTTPS also makes "Copy invite link" work (browsers allow the clipboard only on HTTPS).

## Updating after a merge

```
cd ~/Ourney_TravelSync
git pull
docker compose up -d --build
```

## Daily commands

| Task | Command |
|---|---|
| Status | `docker compose ps` |
| Logs | `docker compose logs -f --tail 100 api` |
| Restart one service | `docker compose restart agent` |
| Stop everything | `docker compose down` (data stays) |
| Memory / disk | `free -h` · `df -h` |

## When the project ends

Lightsail bills until the instance is deleted, even when stopped:
delete the instance, its snapshots and the static IP.
