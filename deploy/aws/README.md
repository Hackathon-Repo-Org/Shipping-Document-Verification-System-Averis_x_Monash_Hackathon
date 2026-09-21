# AWS deployment (EC2 + RDS PostgreSQL)

Live demo: https://shipdoc.duckdns.org

```
Browser
   |  HTTPS
   v
EC2 (Ubuntu 24.04, t3.small, ap-southeast-1)
   +-- web : Caddy. Serves the frontend bundle, gets the HTTPS certificate,
   |         and forwards /api/* to the API. One origin, so no CORS setup.
   +-- api : this repository's Docker image (FastAPI + engine + LLM cache)
   |
   v
RDS PostgreSQL (db.t4g.micro, private, reachable only from the EC2 instance)
```

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Runs the `api` and `web` containers |
| `Caddyfile` | Routes `/api/*` to the backend and everything else to the frontend |

Neither file contains a secret. Secrets live only in `.env` on the server.

## Layout on the server

```
/opt/shipdoc/
  backend/             <- this repository
  frontend/            <- the frontend repository, built into frontend/dist
  docker-compose.yml   <- copied from here
  Caddyfile            <- copied from here
  .env                 <- secrets, chmod 600, never committed
```

`.env` holds:

```
SITE_ADDRESS=<your-domain>          # e.g. shipdoc.duckdns.org, or <ip-with-dashes>.sslip.io
DATABASE_URL=postgresql+psycopg://<user>:<password>@<rds-endpoint>:5432/shipdoc?sslmode=require
DEMO_PASSCODE=<passcode>
CORS_ORIGINS=https://<your-domain>
DEEPSEEK_API_KEY=<optional, for emails not in the committed cache>
```

## Deploy

```bash
cd /opt/shipdoc
sudo docker compose build api
sudo docker compose run --rm --entrypoint alembic api upgrade head
sudo docker compose run --rm api db seed
sudo docker compose run --rm api run --quiet
sudo docker compose up -d api

cd frontend
sudo docker run --rm -v "$PWD":/app -w /app \
  -e VITE_API_BASE_URL="https://<your-domain>" \
  node:20 sh -c "npm ci && npm run build"
cd ..
sudo docker compose up -d web
```

## Update after a push

```bash
# backend
cd /opt/shipdoc/backend && git pull && cd .. && sudo docker compose up -d --build api
# frontend (Caddy serves the new files immediately)
cd /opt/shipdoc/frontend && git pull && sudo docker run --rm -v "$PWD":/app -w /app \
  -e VITE_API_BASE_URL="https://<your-domain>" node:20 sh -c "npm ci && npm run build"
```
