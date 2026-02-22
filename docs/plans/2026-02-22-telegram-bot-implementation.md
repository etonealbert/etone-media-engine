# etone-media-engine Bot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a complete Telegram bot (aiogram 3.x) for managing media assets and orchestrating OpenClaw multi-agent video generation, running locally on Linux.

**Architecture:** SQLite-indexed asset management with domain-separated Routers. FSM-driven upload and generation wizards. CallbackData factories with integer DB IDs to stay under the 64-byte Telegram limit. Global AdminOnlyMiddleware blocks all non-admin users. Mock OpenClaw agent runner with Kling 3.0 stubs and status streaming.

**Tech Stack:** Python 3.11+, aiogram 3.25+, aiosqlite, aiofiles, pydantic-settings, uv

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.env` (gitignored, with real values)
- Modify: `.gitignore`
- Create: all `__init__.py` files for package structure
- Delete from git tracking: `notes_bot_key.txt`

**Step 1: Create `pyproject.toml`**

```toml
[project]
name = "etone-media-engine"
version = "0.1.0"
description = "Telegram-based control center for AI video production pipeline"
requires-python = ">=3.11"
dependencies = [
    "aiogram>=3.25.0",
    "aiosqlite>=0.20.0",
    "aiofiles>=24.1.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.9.0",
]
```

**Step 2: Create `.env.example`**

```env
# Telegram Bot
BOT_TOKEN=your_bot_token_here
ADMIN_ID=128405827

# Paths (relative to project root)
BASE_DIR=.
DB_PATH=data/assets.db

# Planka (optional, for future integration)
PLANKA_BASE_URL=http://localhost:3001
PLANKA_TOKEN=replace_me
```

**Step 3: Create `.env` with real values**

```env
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
ADMIN_ID=128405827
BASE_DIR=.
DB_PATH=data/assets.db
```

**Step 4: Update `.gitignore`**

Append to existing `.gitignore`:
```
# Project-specific
notes_bot_key.txt
data/
assets/
output/
*.db
*.db-journal
```

**Step 5: Create all `__init__.py` files**

Empty `__init__.py` in:
- `bot/`
- `bot/middlewares/`
- `bot/filters/`
- `bot/states/`
- `bot/handlers/`
- `bot/keyboards/`
- `bot/services/`
- `bot/utils/`

**Step 6: Create directories**

```bash
mkdir -p bot/middlewares bot/filters bot/states bot/handlers bot/keyboards bot/services bot/utils
mkdir -p data
```

**Step 7: Remove bot key from git tracking**

```bash
git rm --cached notes_bot_key.txt 2>/dev/null || true
```

**Step 8: Install dependencies**

```bash
uv sync
```

**Step 9: Commit**

```bash
git add -A
git commit -m "feat: scaffold project structure with uv, .env, and package layout"
```

---

### Task 2: Configuration Module

**Files:**
- Create: `bot/config.py`

**Step 1: Write `bot/config.py`**

Uses pydantic-settings to load `.env`. Defines all asset directory paths. Creates directories on import. Uses `SecretStr` for `BOT_TOKEN`. Stores `BASE_DIR` as a `Path` and computes all asset paths from it.

Key fields:
- `bot_token: SecretStr`
- `admin_id: int`
- `base_dir: Path` (default `.`)
- `db_path: Path` (default `data/assets.db`)
- Property: `asset_dirs -> dict[str, Path]` mapping category names to full paths
- Property: `output_dir -> Path`
- Method: `ensure_directories()` that creates all required dirs

Categories dict:
```python
CATEGORIES = {
    "avatars": "assets/avatars",
    "locations": "assets/locations",
    "products": "assets/products",
    "b_rolls": "assets/b_rolls",
    "voiceovers": "assets/voiceovers",
    "audio_sfx": "assets/audio_sfx",
}
```

Subfolder-based categories (require structured subfolders):
```python
SUBFOLDER_CATEGORIES = {"avatars", "locations", "products"}
```

**Step 2: Verify config loads**

```bash
uv run python -c "from bot.config import settings; print(settings.admin_id)"
```
Expected: `128405827`

**Step 3: Commit**

```bash
git add bot/config.py
git commit -m "feat: add pydantic-settings config with asset directory management"
```

---

### Task 3: Database Module

**Files:**
- Create: `bot/database.py`

**Step 1: Write `bot/database.py`**

Async SQLite wrapper using `aiosqlite`. Provides:

- `async def init_db()` - creates table + index if not exists
- `async def upsert_asset(category, folder_name, file_name, file_path, meta_id, meta_name, meta_extra, file_size)` - INSERT OR REPLACE by file_path
- `async def get_assets_by_category(category) -> list[dict]` - returns all assets in category
- `async def get_asset_by_id(asset_id: int) -> dict | None`
- `async def get_unique_folders(category) -> list[dict]` - returns DISTINCT folder_name + meta_id + meta_name for subfolder categories
- `async def get_assets_in_folder(category, folder_name) -> list[dict]`
- `async def clear_category(category)` - delete all rows for a category (used before re-index)
- `async def count_assets(category=None) -> int`

Schema:
```sql
CREATE TABLE IF NOT EXISTS assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    folder_name TEXT,
    file_name   TEXT NOT NULL,
    file_path   TEXT NOT NULL UNIQUE,
    meta_id     TEXT,
    meta_name   TEXT,
    meta_extra  TEXT,
    indexed_at  TEXT NOT NULL,
    file_size   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category);
```

All `file_path` values stored relative to project root. Uses a module-level `_db_path` set from config at init time.

**Step 2: Verify DB initializes**

```bash
uv run python -c "
import asyncio
from bot.database import init_db
asyncio.run(init_db())
print('DB initialized')
"
```
Expected: `DB initialized`, `data/assets.db` file created

**Step 3: Commit**

```bash
git add bot/database.py
git commit -m "feat: add aiosqlite database module for asset indexing"
```

---

### Task 4: File Manager Service

**Files:**
- Create: `bot/services/__init__.py` (already exists, empty)
- Create: `bot/services/file_manager.py`

**Step 1: Write `bot/services/file_manager.py`**

Provides:
- `sanitize_filename(filename: str) -> str` - strip path traversal, replace unsafe chars, truncate to 150 bytes
- `async def generate_save_path(directory: Path, original_name: str) -> Path` - returns `{timestamp}_{sanitized_name}`, handles collisions with counter
- `async def scan_category(category: str) -> list[dict]` - walks the category dir, parses folder names for subfolder categories, returns list of dicts ready for DB upsert
- `parse_folder_name(category: str, folder_name: str) -> dict` - parses structured names like `AV01_Sarah_F_25_Fitness` into `{meta_id, meta_name, meta_extra}`
- `async def get_latest_video() -> Path | None` - finds most recently modified .mp4 in output/videos/
- `async def create_asset_folder(category: str, folder_name: str) -> Path` - creates subfolder with correct structure
- `async def save_uploaded_file(bot, file_id: str, destination: Path)` - downloads telegram file to disk

Uses `aiofiles.os` for non-blocking filesystem operations. Uses `pathlib.Path` throughout. All paths relative to `settings.base_dir`.

Folder name parsing rules:
- avatars: `[ID]_[Name]_[Gender]_[Age]_[Niche]` -> `meta_id=ID, meta_name=Name, meta_extra={"gender":..,"age":..,"niche":..}`
- locations: `[ID]_[Environment]_[Lighting]` -> `meta_id=ID, meta_name=Environment, meta_extra={"lighting":..}`
- products: `[Brand]_[ProductName]` -> `meta_id=Brand, meta_name=ProductName`

**Step 2: Commit**

```bash
git add bot/services/file_manager.py
git commit -m "feat: add async file manager with sanitization and directory scanning"
```

---

### Task 5: Security Middleware

**Files:**
- Create: `bot/middlewares/admin_only.py`

**Step 1: Write `bot/middlewares/admin_only.py`**

```python
class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, admin_id: int): ...
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id == self.admin_id:
            return await handler(event, data)
        return  # silently drop
```

Register as: `dp.update.outer_middleware(AdminOnlyMiddleware(settings.admin_id))`

**Step 2: Commit**

```bash
git add bot/middlewares/admin_only.py
git commit -m "feat: add AdminOnlyMiddleware for global access restriction"
```

---

### Task 6: CallbackData Factories

**Files:**
- Create: `bot/keyboards/callbacks.py`

**Step 1: Write `bot/keyboards/callbacks.py`**

Define all CallbackData factories with short prefixes (to stay under 64 bytes):

```python
class CategoryCB(CallbackData, prefix="cat"):
    action: str     # "select"
    name: str       # "avatars", "locations", etc.

class FolderCB(CallbackData, prefix="fld"):
    action: str     # "new", "existing", "select"
    cat: str        # category short name
    id: int = 0     # DB asset id or 0

class AssetCB(CallbackData, prefix="ast"):
    action: str     # "select", "page"
    id: int = 0     # DB asset id
    page: int = 0   # pagination

class GenerateCB(CallbackData, prefix="gen"):
    action: str     # "avatar", "location", "product", "length", "confirm", "cancel"
    id: int = 0
    page: int = 0

class LengthCB(CallbackData, prefix="len"):
    seconds: int    # 15, 30, 45, 60

class ConfirmCB(CallbackData, prefix="cfm"):
    action: str     # "launch", "cancel"

class CancelCB(CallbackData, prefix="x"):
    pass
```

**Step 2: Commit**

```bash
git add bot/keyboards/callbacks.py
git commit -m "feat: add CallbackData factories with short prefixes"
```

---

### Task 7: Pagination Utility

**Files:**
- Create: `bot/utils/pagination.py`

**Step 1: Write `bot/utils/pagination.py`**

Generic paginator:
```python
def paginate(items: list, page: int = 0, per_page: int = 6) -> tuple[list, bool, bool]:
    """Returns (page_items, has_prev, has_next)"""
```

Used by keyboard builders to slice asset lists and add nav buttons.

**Step 2: Commit**

```bash
git add bot/utils/pagination.py
git commit -m "feat: add generic pagination utility"
```

---

### Task 8: Inline Keyboard Builders

**Files:**
- Create: `bot/keyboards/inline.py`

**Step 1: Write `bot/keyboards/inline.py`**

Functions:
- `category_picker_keyboard() -> InlineKeyboardMarkup` - 6 category buttons in 2 columns + Cancel row. Buttons: [Avatars] [Locations] [Products] [B-Roll] [Voiceovers] [Audio/SFX] [Cancel]
- `folder_choice_keyboard(category: str) -> InlineKeyboardMarkup` - [New Folder] [Existing Folder] [Cancel]
- `async def asset_list_keyboard(category: str, page: int, action_prefix: str) -> InlineKeyboardMarkup` - paginated list of assets/folders from DB, with prev/next buttons
- `async def folder_list_keyboard(category: str, page: int) -> InlineKeyboardMarkup` - list unique folders for subfolder categories
- `length_picker_keyboard() -> InlineKeyboardMarkup` - [15s] [30s] [45s] [60s]
- `confirm_keyboard() -> InlineKeyboardMarkup` - [Launch] [Cancel]

All use `InlineKeyboardBuilder` with `adjust()` for layout. Names truncated to 30 chars for display.

**Step 2: Commit**

```bash
git add bot/keyboards/inline.py
git commit -m "feat: add inline keyboard builders with pagination"
```

---

### Task 9: FSM State Definitions

**Files:**
- Create: `bot/states/upload.py`
- Create: `bot/states/generate.py`

**Step 1: Write `bot/states/upload.py`**

```python
class UploadStates(StatesGroup):
    waiting_category = State()      # file received, waiting for category pick
    waiting_folder_choice = State() # subfolder: new or existing?
    waiting_folder_name = State()   # new folder: enter name
    waiting_folder_select = State() # existing folder: pick from list
```

**Step 2: Write `bot/states/generate.py`**

```python
class GenerateStates(StatesGroup):
    select_avatar = State()
    select_location = State()
    select_product = State()
    enter_task = State()        # Amazon link or description
    select_length = State()     # target video length
    confirm = State()           # review and confirm
```

**Step 3: Commit**

```bash
git add bot/states/
git commit -m "feat: add FSM state definitions for upload and generate flows"
```

---

### Task 10: Common Handlers

**Files:**
- Create: `bot/handlers/common.py`

**Step 1: Write `bot/handlers/common.py`**

Router with handlers for:
- `/start` - welcome message with available commands list
- `/help` - detailed help text explaining all commands and asset rules
- `/cancel` - global cancel handler (works from any FSM state), clears state, sends confirmation

The cancel handler must be registered on the router with `StateFilter("*")` so it catches cancel from any active wizard.

**Step 2: Commit**

```bash
git add bot/handlers/common.py
git commit -m "feat: add /start, /help, /cancel handlers"
```

---

### Task 11: Asset Manager Handlers

**Files:**
- Create: `bot/handlers/asset_manager.py`

**Step 1: Write the upload catch-all handler**

Handler registered with `F.photo | F.document | F.video | F.audio`. Extracts file_id and original filename, stores in FSM state, replies with category picker keyboard.

**Step 2: Write category selection callback handler**

Handles `CategoryCB.filter()`. Reads file_id from FSM state. For subfolder categories (avatars/locations/products): shows folder_choice_keyboard. For flat categories (b_rolls/voiceovers/audio_sfx): downloads immediately to category dir.

**Step 3: Write folder choice handlers**

- "New Folder" callback: sets state to `waiting_folder_name`, asks for folder name
- "Existing Folder" callback: queries DB for existing folders, shows paginated list
- Folder name text handler: creates folder, downloads file into it
- Existing folder select callback: downloads file into selected folder

All download paths: `bot.download(file, destination=str(final_path))`, then auto-index, then confirmation message.

**Step 4: Write `/index` command handler**

- Scans all 6 category directories using `file_manager.scan_category()`
- For each category: clears DB rows, then upserts all found files
- Sends progress updates: "Indexing avatars... (found 5 folders)"
- Final summary: "Index complete. Total: X assets across Y categories"

**Step 5: Write `/create_asset` command handler**

- Parses command args: `/create_asset avatar A 25yo fitness female`
- Validates type is one of the known categories
- For subfolder categories: generates next ID, creates folder structure
- Mocks OpenClaw agent call with `asyncio.sleep()`
- Creates placeholder files in the folder
- Auto-indexes the new folder
- Replies with summary

**Step 6: Write cancel callback handler**

Handles `CancelCB.filter()`, clears FSM state, edits message to "Operation cancelled."

**Step 7: Commit**

```bash
git add bot/handlers/asset_manager.py
git commit -m "feat: add asset manager handlers - upload, /index, /create_asset"
```

---

### Task 12: OpenClaw Service Mock

**Files:**
- Create: `bot/services/openclaw.py`

**Step 1: Write `bot/services/openclaw.py`**

Core function:
```python
async def run_openclaw_agents(
    task_data: dict,
    status_message: Message,
    bot: Bot,
) -> Path | None:
```

Behavior:
1. Build the JSON payload dict with all Kling config fields (documented in comments)
2. Status streaming - edit `status_message` every 3s with agent progress messages
3. Kling polling simulation - edit message every 5s with "Attempt N/10" messages
4. If `task_data["target_length_seconds"] > 15`: add chaining messages
5. Call `get_latest_video()` to find output file
6. Return the video path or None

Include extensive comments explaining:
- Where to inject OpenClaw CLI call
- How to structure the JSON payload for the real API
- Kling Elements/Reference API for face locking
- Prompt structure enforcement: `Scene -> Character -> Action -> Camera -> Style`
- How chaining works (last frame of segment A = first frame of segment B)
- Planka card creation for job tracking

**Step 2: Commit**

```bash
git add bot/services/openclaw.py
git commit -m "feat: add OpenClaw agent mock with Kling API stubs and status streaming"
```

---

### Task 13: Generate Handlers

**Files:**
- Create: `bot/handlers/generate.py`

**Step 1: Write `/generate` command handler**

Starts FSM, sets state to `select_avatar`, queries DB for avatars, shows paginated keyboard.

**Step 2: Write avatar selection callback**

Handles `GenerateCB.filter(action="avatar")`. Stores selected avatar data in FSM. Moves to `select_location` state, shows locations keyboard.

**Step 3: Write location selection callback**

Same pattern. Stores location, moves to `select_product`.

**Step 4: Write product selection callback**

Stores product, moves to `enter_task`, asks for Amazon link or description.

**Step 5: Write task text handler**

Receives free text in `enter_task` state. Stores it. Moves to `select_length`, shows length picker keyboard.

**Step 6: Write length selection callback**

Handles `LengthCB.filter()`. If seconds > 15: sends chaining warning. Stores length. Moves to `confirm` state, shows summary of all selections + confirm keyboard.

**Step 7: Write confirm/cancel callbacks**

- Confirm: calls `run_openclaw_agents()`, then sends video via `FSInputFile` if found
- Cancel: clears state, edits message

**Step 8: Write pagination callbacks for generate flow**

Handle page navigation within each asset selection step.

**Step 9: Commit**

```bash
git add bot/handlers/generate.py
git commit -m "feat: add /generate FSM wizard with 5-step asset selection"
```

---

### Task 14: Loader and Entry Point

**Files:**
- Create: `bot/loader.py`
- Create: `bot/__main__.py`

**Step 1: Write `bot/loader.py`**

Factory functions:
- `create_bot() -> Bot` - creates Bot with DefaultBotProperties (HTML parse mode)
- `create_dispatcher() -> Dispatcher` - creates Dispatcher with MemoryStorage, registers middleware, includes all routers in correct order (common first for cancel priority)

Router include order:
1. `common_router` (cancel handler has priority)
2. `asset_manager_router`
3. `generate_router`

Middleware registration:
```python
dp.update.outer_middleware(AdminOnlyMiddleware(settings.admin_id))
```

**Step 2: Write `bot/__main__.py`**

```python
async def main():
    await init_db()
    settings.ensure_directories()
    bot = create_bot()
    dp = create_dispatcher()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
```

Also set bot commands via `bot.set_my_commands()`:
- `/start` - Start bot
- `/help` - Show help
- `/index` - Reindex asset library
- `/create_asset` - Create new asset folder
- `/generate` - Generate video
- `/cancel` - Cancel current operation

**Step 3: Commit**

```bash
git add bot/loader.py bot/__main__.py
git commit -m "feat: add bot loader and entry point with router assembly"
```

---

### Task 15: Final Integration and .gitignore Cleanup

**Files:**
- Modify: `.gitignore`
- Modify: `README.md` (brief update, not full rewrite)

**Step 1: Verify `.gitignore` covers all sensitive/generated files**

Ensure these are covered:
```
.env
notes_bot_key.txt
data/
assets/
output/
*.db
*.db-journal
```

**Step 2: Test full bot startup**

```bash
uv run python -m bot
```

Expected: Bot starts polling, logs show "Bot started" or similar.

**Step 3: Manual smoke test via Telegram**

1. Send `/start` - should get welcome message
2. Send a photo - should get category picker
3. Send `/index` - should scan and report counts
4. Send `/generate` - should start FSM wizard (may show empty if no assets indexed)
5. Send `/cancel` - should cancel any active state

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete etone-media-engine bot v0.1.0"
```

---

## Execution Notes

- **Total tasks:** 15
- **Estimated time:** 60-90 minutes for subagent execution
- **Dependencies:** Tasks are strictly sequential (each builds on previous)
- **Parallelizable:** Tasks 5-7 (middleware, callbacks, pagination) are independent of each other and can run in parallel
- **Critical path:** Task 1 -> 2 -> 3 -> 4 -> 11 -> 12 -> 13 -> 14
