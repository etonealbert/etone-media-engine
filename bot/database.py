"""
SQLite database module for asset indexing.
Uses aiosqlite for non-blocking async access.
All file_path values are stored relative to project root for portability.
"""

import json
from datetime import datetime, timezone

import aiosqlite

from bot.config import settings

_db_path: str = ""


async def init_db() -> None:
    """Initialize the database: create tables and indexes if they don't exist."""
    global _db_path
    _db_path = str(settings.db_full_path)

    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
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
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category)")
        await db.commit()


def _row_to_dict(row: aiosqlite.Row, cursor: aiosqlite.Cursor) -> dict:
    """Convert a database row to a dictionary."""
    columns = [desc[0] for desc in cursor.description]
    d = dict(zip(columns, row))
    # Parse meta_extra JSON if present
    if d.get("meta_extra"):
        try:
            d["meta_extra"] = json.loads(d["meta_extra"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


async def upsert_asset(
    category: str,
    folder_name: str | None,
    file_name: str,
    file_path: str,
    meta_id: str | None = None,
    meta_name: str | None = None,
    meta_extra: dict | None = None,
    file_size: int | None = None,
) -> int | None:
    """Insert or replace an asset record. Returns the row id."""
    now = datetime.now(timezone.utc).isoformat()
    extra_json = json.dumps(meta_extra) if meta_extra else None

    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO assets (category, folder_name, file_name, file_path,
                                meta_id, meta_name, meta_extra, indexed_at, file_size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                category   = excluded.category,
                folder_name = excluded.folder_name,
                file_name  = excluded.file_name,
                meta_id    = excluded.meta_id,
                meta_name  = excluded.meta_name,
                meta_extra = excluded.meta_extra,
                indexed_at = excluded.indexed_at,
                file_size  = excluded.file_size
            """,
            (
                category,
                folder_name,
                file_name,
                file_path,
                meta_id,
                meta_name,
                extra_json,
                now,
                file_size,
            ),
        )
        await db.commit()
        return cursor.lastrowid or 0


async def get_assets_by_category(category: str) -> list[dict]:
    """Return all assets in a category, ordered by folder then filename."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM assets WHERE category = ? ORDER BY folder_name, file_name",
            (category,),
        )
        rows = await cursor.fetchall()
        return [_row_to_dict(row, cursor) for row in rows]


async def get_asset_by_id(asset_id: int) -> dict | None:
    """Return a single asset by its database ID."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
        row = await cursor.fetchone()
        return _row_to_dict(row, cursor) if row else None


async def get_unique_folders(category: str) -> list[dict]:
    """Return distinct folders for subfolder-based categories with parsed metadata."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT MIN(id) AS id, folder_name, meta_id, meta_name, meta_extra
            FROM assets
            WHERE category = ? AND folder_name IS NOT NULL
            GROUP BY folder_name, meta_id, meta_name, meta_extra
            ORDER BY meta_id, folder_name
            """,
            (category,),
        )
        rows = await cursor.fetchall()
        return [_row_to_dict(row, cursor) for row in rows]


async def get_assets_in_folder(category: str, folder_name: str) -> list[dict]:
    """Return all assets within a specific folder of a category."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM assets WHERE category = ? AND folder_name = ? ORDER BY file_name",
            (category, folder_name),
        )
        rows = await cursor.fetchall()
        return [_row_to_dict(row, cursor) for row in rows]


async def clear_category(category: str) -> int:
    """Delete all rows for a category. Returns number of deleted rows."""
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute("DELETE FROM assets WHERE category = ?", (category,))
        await db.commit()
        return cursor.rowcount


async def count_assets(category: str | None = None) -> int:
    """Count assets, optionally filtered by category."""
    async with aiosqlite.connect(_db_path) as db:
        if category:
            cursor = await db.execute("SELECT COUNT(*) FROM assets WHERE category = ?", (category,))
        else:
            cursor = await db.execute("SELECT COUNT(*) FROM assets")
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_next_meta_id(category: str, prefix: str) -> str:
    """Generate the next sequential ID for a category (e.g., AV01 -> AV02)."""
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            """
            SELECT meta_id FROM assets
            WHERE category = ? AND meta_id LIKE ?
            ORDER BY meta_id DESC LIMIT 1
            """,
            (category, f"{prefix}%"),
        )
        row = await cursor.fetchone()

    if not row or not row[0]:
        return f"{prefix}01"

    last_id = row[0]
    # Extract numeric part
    num_part = "".join(c for c in last_id if c.isdigit())
    if num_part:
        next_num = int(num_part) + 1
        return f"{prefix}{next_num:02d}"
    return f"{prefix}01"
