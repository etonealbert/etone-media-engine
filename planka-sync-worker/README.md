# planka-sync-worker

Local worker for **Planka -> Telegram** sync notifications (card move detection).

## What this worker does
- Connects to local/self-hosted Planka API.
- Polls projects -> boards -> cards.
- Detects when a card changes list (`listId`).
- Sends Telegram message to a configured chat.

## Why this design
- Works even when direct Planka webhooks are unavailable.
- Safe for local-only setups.
- Easy to extend with create/update/assign/comment events.

## Files
- `worker.py` — main polling worker.
- `.env.example` — required config.
- `requirements.txt` — python dependencies.

## Quick start
```bash
cd planka-sync-worker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill .env values
python worker.py
```

## Required env vars
- `PLANKA_BASE_URL` — eg `http://localhost:3001`
- `PLANKA_TOKEN` — API token
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional:
- `PLANKA_PROJECT_IDS` — comma-separated project IDs to monitor
- `POLL_INTERVAL_SECONDS` — default 20
- `VERBOSE_NOTIFICATIONS` — heartbeat messages

## Notes for developer
Planka API versions can differ. If needed, adapt these methods in `PlankaClient`:
- `list_projects()`
- `list_boards(project_id)`
- `list_cards(board_id)`

The worker currently assumes common REST patterns:
- `GET /api/projects`
- `GET /api/projects/{id}/boards`
- `GET /api/boards/{id}/cards`

If your deployment exposes different endpoint shapes, update those three methods only.

## Suggested production hardening
1. Persist state (`card_id -> list_id`) to SQLite/Redis (currently in-memory).
2. Add retry/backoff and timeout metrics.
3. Add per-project rate limiting.
4. Add structured logs (JSON).
5. Add systemd service for auto-restart.
