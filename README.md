# etone-media-engine

Private Telegram-based control center for AI-powered UGC video production. Manages structured media assets, orchestrates a 5-agent pipeline via OpenClaw, and interfaces with Kling 3.0 for AI video generation — all from a single Telegram chat.

## How It Works

```
You (Telegram) --> etone-media-engine bot --> OpenClaw Gateway
                        |                         |
                   Asset Library            Agent Pipeline
                   (local disk)            (5 sequential agents)
                        |                         |
                   SQLite Index         Analyst --> Scriptwriter
                                        --> Prompt Engineer
                                        --> Kling 3.0 Video Gen
                                        --> Assembly Manager
                                                  |
                                            output/videos/
                                                  |
                                        Video sent back to you
```

Drop files into the chat to build your asset library. Run `/generate` to walk through a wizard that selects avatar, location, product, task description, and video length — then the bot launches the agent pipeline and streams status updates until your video is ready.

## Quick Start

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/)

```bash
# Clone
git clone https://github.com/etonealbert/etone-media-engine.git
cd etone-media-engine

# Configure
cp .env.example .env
# Edit .env with your BOT_TOKEN and ADMIN_ID

# Install and run
uv sync
uv run python -m bot
```

The bot creates all required directories on first start (`assets/`, `output/`, `data/`).

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | Yes | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_ID` | Yes | Your Telegram user ID (get from [@userinfobot](https://t.me/userinfobot)) |
| `BASE_DIR` | No | Project root path (default: `.`) |
| `DB_PATH` | No | SQLite database path (default: `data/assets.db`) |

Future pipeline variables (not needed until agents are connected):

| Variable | Agent |
|----------|-------|
| `OPENAI_API_KEY` | Analyst, Prompt Engineer |
| `ANTHROPIC_API_KEY` | Scriptwriter |
| `KLING_API_KEY` | Video Generator |
| `ELEVENLABS_API_KEY` | Voiceover generation (optional) |
| `PLANKA_BASE_URL` | Kanban board integration |
| `PLANKA_TOKEN` | Kanban board integration |

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message with command overview |
| `/help` | Detailed help — asset rules, naming conventions, Kling constraints |
| `/index` | Scan all `assets/` directories and rebuild the SQLite index |
| `/create_asset [type] [description]` | Create a new asset folder with structured naming |
| `/generate` | Launch the 5-step video generation wizard |
| `/cancel` | Cancel any active operation |

### File Upload

Send any file (photo, video, audio, document) directly to the chat without a command. The bot catches it and presents an inline keyboard:

```
Where should I save this file?

[Avatars]    [Locations]
[Products]   [B-Roll]
[Voiceovers] [Audio/SFX]
       [Cancel]
```

For subfolder categories (avatars, locations, products), it asks whether to create a new folder or save to an existing one. Files are saved with a `{timestamp}_{original_name}` format.

### /create_asset Examples

```
/create_asset avatar A 25yo fitness female
  --> Creates: assets/avatars/AV02_A_F_25_Fitness/

/create_asset location Modern kitchen with morning sun
  --> Creates: assets/locations/LOC01_Modern_kitchen_with_morning_sun/

/create_asset product Ghost Creatine
  --> Creates: assets/products/Ghost_Creatine/
```

### /generate Wizard

A 5-step FSM wizard with inline keyboards at each step:

1. **Select Avatar** — paginated list from database index
2. **Select Location** — paginated list from database index
3. **Select Product** — paginated list from database index
4. **Enter Task** — Amazon product URL or freeform description
5. **Select Length** — `[15s] [30s] [45s] [60s]` (warns about chaining if >15s)
6. **Confirm** — summary of all selections, `[Launch]` or `[Cancel]`

After launch, the bot streams agent status updates by editing a single message every 3 seconds.

## Project Structure

```
etone-media-engine/
├── bot/                           # Main bot package
│   ├── __main__.py                # Entry point (uv run python -m bot)
│   ├── config.py                  # pydantic-settings, .env loader, path management
│   ├── loader.py                  # Bot + Dispatcher factory, router assembly
│   ├── database.py                # aiosqlite — asset index CRUD
│   ├── middlewares/
│   │   └── admin_only.py          # Global outer middleware (blocks non-admin users)
│   ├── handlers/
│   │   ├── common.py              # /start, /help, /cancel
│   │   ├── asset_manager.py       # File upload, /index, /create_asset
│   │   └── generate.py            # /generate FSM wizard + video delivery
│   ├── keyboards/
│   │   ├── callbacks.py           # CallbackData factories (7 types, short prefixes)
│   │   └── inline.py              # Keyboard builders with pagination
│   ├── states/
│   │   ├── upload.py              # UploadStates FSM
│   │   └── generate.py            # GenerateStates FSM
│   ├── services/
│   │   ├── file_manager.py        # Async file ops, sanitization, directory scanning
│   │   └── openclaw.py            # Agent runner mock + Kling 3.0 stubs
│   └── utils/
│       └── pagination.py          # Generic paginator for inline keyboards
│
├── assets/                        # Media asset library (gitignored contents)
│   ├── avatars/                   # Subfolder-based: [ID]_[Name]_[Gender]_[Age]_[Niche]
│   ├── locations/                 # Subfolder-based: [ID]_[Environment]_[Lighting]
│   ├── products/                  # Subfolder-based: [Brand]_[ProductName]
│   ├── b_rolls/                   # Flat: [Niche]_[Action]_[Setting].mp4
│   ├── voiceovers/                # Flat: clean dry speech for Kling lip-sync
│   ├── audio_sfx/                 # Flat: music + SFX for post-production only
│   └── EXAMPLE.md                 # Naming conventions and file specs reference
│
├── output/videos/                 # Generated video output
├── data/assets.db                 # SQLite asset index (auto-created)
│
├── planka-sync-worker/            # Separate service: Planka -> Telegram notifications
│   ├── worker.py                  # Synchronous polling worker
│   ├── .env.example               # Config template
│   └── README.md                  # Setup docs
│
├── docs/
│   ├── AGENTS.md                  # Agent pipeline specification (5 agents)
│   └── plans/                     # Architecture design + implementation plan
│
├── .env.example                   # Environment variable template
├── .projectrules                  # LLM context for AI-assisted development
├── pyproject.toml                 # uv project config + dependencies
└── uv.lock                       # Locked dependency versions
```

## Asset Library

The bot enforces a structured asset architecture. Three categories use subfolders with parsed naming conventions; three use flat file storage.

### Subfolder Categories

| Category | Folder Naming | Required Files |
|----------|--------------|----------------|
| **Avatars** | `AV01_Sarah_F_25_Fitness` | `front_medium.png`, `profile_left.png`, `profile_right.png`, `full_body.png`, `portrait/headshot_01.png`, `voice/voice_ref.wav`, `voice/voice_profile.json`, `character.json` |
| **Locations** | `LOC01_ModernKitchen_MorningSun` | `wide_shot.png`, `medium_shot.png`, `close_up.png` |
| **Products** | `Ghost_Creatine` | `product_front.png` (transparent BG), `product_side.png`, `label_flat.jpg` |

### Flat Categories

| Category | File Naming | Specs |
|----------|-------------|-------|
| **B-Rolls** | `Fitness_MixingPowder_Kitchen.mp4` | MP4, 9:16, 3-8 seconds, no faces |
| **Voiceovers** | `Hook_Creatine_Take1.wav` | WAV, clean dry audio, NO background music |
| **Audio/SFX** | `Whoosh_Fast.wav` / `Music_Phonk_Gym.mp3` | WAV or MP3, royalty-free |

> **Important:** Voiceovers and Audio/SFX are strictly separated. Kling 3.0 requires clean, dry audio for lip-sync. Background music is added only during final assembly.

Avatar folders are treated as full character packages (visual + voice + persona). During generation, the bot passes avatar portrait, voice profile/reference, and `character.json` into the HITL handoff payload so identity/voice remains consistent across videos.

See [`assets/EXAMPLE.md`](assets/EXAMPLE.md) for full specs.

## Architecture

### Security

A global `AdminOnlyMiddleware` is registered as an outer middleware on `dp.update`. It intercepts every incoming update (messages, callbacks, inline queries) and silently drops anything from a user whose ID does not match `ADMIN_ID`. No handler code runs for unauthorized users.

### SQLite Asset Index

Inline keyboards are built from database queries, not filesystem reads. The `/index` command scans `assets/` directories, parses folder names, and upserts records into SQLite. All keyboard builders use integer database IDs in callback data (never file paths), staying under Telegram's 64-byte callback_data limit.

### FSM (Finite State Machine)

Two FSM flows manage multi-step interactions:

- **UploadStates** — file categorization wizard (category > new/existing folder > save)
- **GenerateStates** — video generation wizard (avatar > location > product > task > length > confirm)

Uses `MemoryStorage` (sufficient for a single-admin bot). Swap to `RedisStorage` for persistence across restarts.

### CallbackData Factories

Seven factory types with short prefixes to maximize payload space:

| Prefix | Factory | Use |
|--------|---------|-----|
| `cat` | CategoryCB | Upload category selection |
| `fld` | FolderCB | Folder new/existing/select |
| `ast` | AssetCB | Asset selection + pagination |
| `gen` | GenerateCB | Generate wizard selections |
| `len` | LengthCB | Video length picker |
| `cfm` | ConfirmCB | Launch/cancel confirmation |
| `x` | CancelCB | Universal cancel |

## Agent Pipeline

The bot orchestrates 5 agents that execute sequentially. Currently mocked — replace `bot/services/openclaw.py` when connecting to the real OpenClaw Gateway.

| # | Agent | Type | Model | What It Does |
|---|-------|------|-------|-------------|
| 1 | **Analyst** | LLM | GPT-4o | Parses Amazon listings or descriptions, extracts customer pain points, generates hooks |
| 2 | **Scriptwriter** | LLM | Claude Sonnet | Writes timed script segments (each <=15s), hooks, CTA |
| 3 | **Prompt Engineer** | LLM | GPT-4o | Generates Kling prompts with strict `Scene>Character>Action>Camera>Style` ordering |
| 4 | **Video Generator** | API | Kling 3.0 | Submits prompts, polls for completion, handles chaining for videos >15s |
| 5 | **Assembly Manager** | Tool | FFmpeg | Concatenates segments, adds BGM + SFX, exports final MP4 |

See [`docs/AGENTS.md`](docs/AGENTS.md) for complete agent definitions, input/output schemas, OpenClaw configs, and the full JSON payload specification.

### Kling 3.0 Constraints

These are enforced in the bot's logic and documented in the agent payload:

- **15-second segment limit** — videos longer than 15s use chaining (last frame of segment N = start frame of segment N+1)
- **Elements/Reference API** — locks avatar facial features from `front_medium.png` across all segments
- **Prompt layer order** — must be: `Scene -> Character -> Action -> Camera -> Style`
- **Audio separation** — lip-sync requires clean dry audio from `voiceovers/`; background music from `audio_sfx/` is added only in post-production
- **Async polling** — Kling API returns a task ID; must poll status endpoint until completion

## Planka Integration

A separate `planka-sync-worker/` service polls a self-hosted Planka Kanban board and sends Telegram notifications when cards move between lists. This runs independently from the bot as a synchronous polling loop.

Future integration: the bot will create Planka cards for each video generation job, and agents will update card status as they complete their work.

See [`PLANKA_SYNC_WORKER_BRIEF.md`](PLANKA_SYNC_WORKER_BRIEF.md) for setup.

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Bot framework | aiogram 3.25+ | Async, Router-based, modern Python, Bot API 9.4 support |
| Database | aiosqlite | Non-blocking SQLite access, zero infrastructure |
| File I/O | aiofiles | Non-blocking filesystem operations |
| Config | pydantic-settings | Type-safe `.env` loading, fail-fast validation |
| Package manager | uv | Fast installs, lockfile support, reproducible builds |
| Python | 3.11+ | Required for modern type syntax (`dict | None`, etc.) |

## Development

```bash
# Install with dev dependencies
uv sync --all-extras

# Lint
uv run ruff check bot/

# Format
uv run ruff format bot/

# Run tests
uv run pytest
```

### Adding a New Handler

1. Create a new `Router()` in `bot/handlers/your_handler.py`
2. Export the router
3. Include it in `bot/loader.py`: `dp.include_router(your_handler.router)`

### Adding a New Callback Type

1. Add a new `CallbackData` subclass in `bot/keyboards/callbacks.py` (keep prefix to 1-3 chars)
2. Build keyboard using `InlineKeyboardBuilder` in `bot/keyboards/inline.py`
3. Handle the callback in your handler with `YourCB.filter()`

### Connecting Real Agents

Replace the mock in `bot/services/openclaw.py`. The function signature and status streaming pattern stay the same — just swap `asyncio.sleep()` calls for real OpenClaw Gateway API calls. The JSON payload structure is fully documented in the comments and in [`docs/AGENTS.md`](docs/AGENTS.md).

## License

Private repository. All rights reserved.
