# Planka Sync Worker — Developer Handoff

## Objective
Bridge local Planka board activity to local Telegram bot notifications.

## Current implementation
Location:
- `planka-sync-worker/worker.py`

Behavior:
- Poll loop every `POLL_INTERVAL_SECONDS` (default 20s)
- Reads projects -> boards -> cards
- Compares current `listId` with previous state
- On change, sends Telegram message

Message payload example:
- project name
- board name
- card name
- from list id
- to list id

## Config contract
Environment variables:
- `PLANKA_BASE_URL`
- `PLANKA_TOKEN`
- `PLANKA_AUTH_HEADER` (default `Authorization`)
- `PLANKA_AUTH_PREFIX` (default `Bearer`)
- `PLANKA_PROJECT_IDS` (optional filter)
- `POLL_INTERVAL_SECONDS`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `VERBOSE_NOTIFICATIONS`

Template:
- `planka-sync-worker/.env.example`

## Assumed API routes (adjust per deployment)
- `GET /api/projects`
- `GET /api/projects/{id}/boards`
- `GET /api/boards/{id}/cards`

If Planka route schema differs, patch only:
- `PlankaClient.list_projects`
- `PlankaClient.list_boards`
- `PlankaClient.list_cards`

## Runtime
```bash
cd planka-sync-worker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python worker.py
```

## Known limitations (intended first version)
- In-memory state only (resets on restart)
- Uses list IDs in notifications; list-name lookup can be added
- Polling only; no webhook intake path yet

## Recommended next steps
1. Persist state in SQLite.
2. Add list-name resolver for human-friendly messages.
3. Add webhook receiver endpoint if Planka/webhook becomes available.
4. Add systemd service unit for auto-restart.
5. Add command channel for bot (/watch, /mute, /status).
