# Clipping Factory

An autonomous operation for paid clipping campaigns:
**discover → score → select → produce → QC → publish → submit → monitor → optimize.**
Start with `PROJECT_STATUS.md`. Platform facts are in `INTEGRATIONS.md`, design in `ARCHITECTURE.md`, and open work in `TASKS.md`.

## Quick start (local)
```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env && chmod 600 .env       # fill in what you have
.venv/bin/factory init
.venv/bin/factory discover                   # live campaigns from Content Rewards + Vyro
.venv/bin/factory campaigns                  # ranked, with expected net
.venv/bin/factory integrations --test        # writes INTEGRATION_STATUS.md
.venv/bin/python -m pytest -q                # 22 tests
```
Produce from an authorised source file:
```bash
factory produce --campaign 2 --file brief_video.mp4 --rights "Official footage from campaign brief <url>"
```

## Deploy 24/7 (any Linux VPS, 4 GB RAM+)
```bash
git clone <your repo> clipping-factory && cd clipping-factory
cp .env.example .env && chmod 600 .env && nano .env
docker compose up -d --build
docker compose exec worker factory init
```
- `worker`, `bot`, `web`, `db` all restart automatically. State lives in Postgres + `./data`. The DB is backed up daily to `data/backups/`, keeping 14.
- Dashboard listens on `127.0.0.1:8080`. Reach it with `ssh -L 8080:localhost:8080 server`, then open
  `http://localhost:8080/login?token=$DASHBOARD_TOKEN`, or put it behind an HTTPS reverse proxy.
- Monitoring: `/status` in Telegram, `GET /healthz`, `docker compose ps`, `docker compose logs -f worker`.
- Emergency stop: `/stopall` (Telegram) or the Automation page.

## How you interact day to day
The bot only messages you when something needs you: a selected campaign to join or a source file to add, a post to
publish manually (`/posted <id> <url>`), a submission and its deadline (`/submitted <id>`), approvals (`/approve <id>`),
rejections (`/rejected …`), and payouts (`/paid …`). Use `/help` for the full command list.
