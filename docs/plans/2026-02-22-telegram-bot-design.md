# etone-media-engine: Telegram Bot Architecture Design

**Date:** 2026-02-22
**Status:** Approved
**Approach:** SQLite-Indexed, Domain-Router Architecture (Approach 2)

## Overview

Telegram bot (aiogram 3.x) serving as the human-in-the-loop control center for a multi-agent UGC video production pipeline. Single-admin, locally hosted on Linux. Manages structured media assets, orchestrates OpenClaw agent workflows, and handles Kling 3.0 API integration stubs.

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Package manager | uv + pyproject.toml | Fast, lockfile support, recommended by aiogram docs |
| Asset index | aiosqlite | Fast keyboard queries without disk I/O; integer IDs solve 64-byte callback limit |
| FSM storage | MemoryStorage (dev) | Single-admin bot; add RedisStorage fallback comment for production |
| Security | Outer middleware on dp.update | Covers all update types globally before any router |
| File paths in DB | Relative to project root | Portable if project directory moves |
| Config | pydantic-settings + .env | Type-safe, fail-fast, SecretStr for tokens |

## Project File Tree

```
etone-media-engine/
├── bot/
│   ├── __init__.py
│   ├── __main__.py               # Entry point: python -m bot
│   ├── config.py                 # Pydantic Settings
│   ├── loader.py                 # Bot + Dispatcher + Storage factory
│   ├── database.py               # aiosqlite schema + CRUD
│   ├── middlewares/
│   │   ├── __init__.py
│   │   └── admin_only.py
│   ├── filters/
│   │   ├── __init__.py
│   │   └── admin.py
│   ├── states/
│   │   ├── __init__.py
│   │   ├── upload.py
│   │   └── generate.py
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── common.py             # /start, /help, /cancel
│   │   ├── asset_manager.py      # /index, /create_asset, file upload
│   │   └── generate.py           # /generate FSM + OpenClaw workflow
│   ├── keyboards/
│   │   ├── __init__.py
│   │   ├── inline.py
│   │   └── callbacks.py          # CallbackData factories
│   ├── services/
│   │   ├── __init__.py
│   │   ├── file_manager.py       # Async file ops
│   │   └── openclaw.py           # Agent runner mock + Kling stubs
│   └── utils/
│       ├── __init__.py
│       └── pagination.py
├── assets/                       # Auto-created, gitignored
│   ├── avatars/
│   ├── locations/
│   ├── products/
│   ├── b_rolls/
│   ├── voiceovers/
│   └── audio_sfx/
├── output/videos/                # Auto-created, gitignored
├── data/                         # Auto-created, gitignored
│   └── assets.db
├── planka-sync-worker/           # Existing, untouched
├── .env                          # Gitignored
├── .env.example
├── pyproject.toml
└── .gitignore                    # Updated
```

## Security: AdminOnlyMiddleware

Global outer middleware on `dp.update`. Intercepts every update type before any router.

```
Telegram Update -> AdminOnlyMiddleware -> [user.id == ADMIN_ID?]
                                           ├── YES -> handler chain
                                           └── NO  -> silently drop
```

Uses `data.get("event_from_user")` which works for messages, callbacks, inline queries.

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    folder_name TEXT,
    file_name   TEXT NOT NULL,
    file_path   TEXT NOT NULL UNIQUE,  -- relative to project root
    meta_id     TEXT,
    meta_name   TEXT,
    meta_extra  TEXT,                  -- JSON
    indexed_at  TEXT NOT NULL,
    file_size   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category);
```

All `file_path` values stored relative to project root (e.g. `assets/avatars/AV01_Sarah_F_25_Fitness/front_medium.png`).

## Asset Directory Rules

| Category | Structure | Folder Naming | Required Files |
|----------|-----------|---------------|----------------|
| avatars | Subfolder-based | `[ID]_[Name]_[Gender]_[Age]_[Niche]` | front_medium.png, profile.png |
| locations | Subfolder-based | `[ID]_[Environment]_[Lighting]` | wide_shot.png, medium_shot.png |
| products | Subfolder-based | `[Brand]_[ProductName]` | product_front.png |
| b_rolls | Flat | `[Niche]_[Action]_[Setting].mp4` | - |
| voiceovers | Flat | `[Category]_[Name].wav` | - |
| audio_sfx | Flat | `[Category]_[Name].wav/.mp3` | - |

## Upload Flow

```
User drops file -> catch-all (F.photo | F.document | F.video | F.audio)
  -> Store file_id + original_name in FSM
  -> Category keyboard: [Avatars] [Locations] [Products] [B-Roll] [Voiceovers] [Audio/SFX] [Cancel]
  -> User picks category
  -> For subfolder categories: ask "New folder or Existing?"
     -> New: ask folder name
     -> Existing: paginated subfolder list from DB
  -> Download + save as {timestamp}_{sanitized_name}
  -> Auto re-index category
  -> Confirm message
```

## /create_asset Flow

```
/create_asset avatar A 25yo fitness female
  -> Parse: type="avatar", description="A 25yo fitness female"
  -> Generate next ID: scan DB for max avatar ID, increment (AV01 -> AV02)
  -> Create folder: assets/avatars/AV02_Unknown_F_25_Fitness/
  -> Mock OpenClaw call: simulate image generation
  -> Save placeholder files into folder
  -> Auto-index new folder
  -> Reply with folder contents summary
```

Supported types: `avatar`, `location`, `product`, `b_roll`, `voiceover`, `sfx`

## /generate FSM Wizard

```
/generate
  Step 1: SELECT AVATAR    -> paginated keyboard from DB
  Step 2: SELECT LOCATION  -> paginated keyboard from DB
  Step 3: SELECT PRODUCT   -> paginated keyboard from DB
  Step 4: AMAZON LINK      -> free text
  Step 5: TARGET LENGTH    -> [15s] [30s] [45s] [60s]
           if >15s: warn about Kling chaining
  CONFIRM: summary -> [Launch] [Cancel]
  Launch -> run_openclaw_agents(task_data)
```

## OpenClaw Agent Mock

`run_openclaw_agents(task_data, status_message, bot)`:

1. Status streaming (edit single message every 3s):
   - "Analyst parsing customer pain points..."
   - "Scriptwriter writing hooks..."
   - "Prompt Engineer working with Kling 3.0..."
   - "Kling API rendering (Attempt N/10)..." (5s intervals, polling simulation)
   - "Manager assembling video..."

2. JSON payload structure for future integration:
```json
{
    "avatar": {"path": "...", "folder": "AV01_..."},
    "location": {"path": "...", "folder": "LOC01_..."},
    "product": {"path": "...", "folder": "Ghost_..."},
    "task_description": "...",
    "target_length_seconds": 30,
    "kling_config": {
        "use_elements_locking": true,
        "prompt_structure": "Scene -> Character -> Action -> Camera -> Style",
        "max_segment_seconds": 15,
        "chain_segments": true
    }
}
```

3. Find latest .mp4 in output/videos/, send via FSInputFile.

## Dependencies

- aiogram>=3.25.0
- aiosqlite>=0.20.0
- aiofiles>=24.1.0
- pydantic>=2.0
- pydantic-settings>=2.0
- python-dotenv>=1.0.0

## .env Variables

```
BOT_TOKEN=<telegram bot token>
ADMIN_ID=128405827
BASE_DIR=.
DB_PATH=data/assets.db
```
