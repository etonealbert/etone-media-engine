"""
Async file management service.
Handles file sanitization, directory scanning, folder name parsing,
and safe file operations using aiofiles for non-blocking I/O.
"""

import os
import re
from datetime import datetime
from pathlib import Path

import aiofiles.os

from bot.config import SUBFOLDER_CATEGORIES, settings


def sanitize_filename(filename: str) -> str:
    """
    Transform a filename into a safe Linux-compatible string.
    Removes path traversal attempts and shell metacharacters.
    """
    # Extract only the base name to prevent directory traversal
    name = os.path.basename(filename)
    # Replace anything not alphanumeric, dot, underscore, or hyphen
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    # Remove leading dots (hidden files)
    name = name.lstrip(".")
    # Ensure non-empty
    if not name:
        name = "unnamed_file"
    # Truncate to 150 chars to leave room for timestamp prefix
    return name[:150]


async def generate_save_path(directory: Path, original_name: str) -> Path:
    """
    Generate a unique save path with timestamp prefix.
    Handles naming collisions with an incrementing counter.
    Returns: Path like directory/20260222_143015_original_name.ext
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized = sanitize_filename(original_name)
    base_name = f"{timestamp}_{sanitized}"
    target = directory / base_name

    counter = 1
    while await aiofiles.os.path.exists(str(target)):
        stem = Path(base_name).stem
        suffix = Path(base_name).suffix
        target = directory / f"{stem}_{counter}{suffix}"
        counter += 1

    return target


def parse_folder_name(category: str, folder_name: str) -> dict:
    """
    Parse structured folder names into metadata fields.

    Rules:
    - avatars:   [ID]_[Name]_[Gender]_[Age]_[Niche]  -> AV01_Sarah_F_25_Fitness
    - locations: [ID]_[Environment]_[Lighting]        -> LOC01_ModernKitchen_MorningSun
    - products:  [Brand]_[ProductName]                -> Ghost_Creatine
    """
    parts = folder_name.split("_")

    if category == "avatars" and len(parts) >= 2:
        meta_id = parts[0] if parts else None
        meta_name = parts[1] if len(parts) > 1 else None
        meta_extra = {}
        if len(parts) > 2:
            meta_extra["gender"] = parts[2]
        if len(parts) > 3:
            meta_extra["age"] = parts[3]
        if len(parts) > 4:
            meta_extra["niche"] = "_".join(parts[4:])
        return {"meta_id": meta_id, "meta_name": meta_name, "meta_extra": meta_extra}

    elif category == "locations" and len(parts) >= 2:
        meta_id = parts[0] if parts else None
        meta_name = parts[1] if len(parts) > 1 else None
        meta_extra = {}
        if len(parts) > 2:
            meta_extra["lighting"] = "_".join(parts[2:])
        return {"meta_id": meta_id, "meta_name": meta_name, "meta_extra": meta_extra}

    elif category == "products" and len(parts) >= 2:
        meta_id = parts[0]  # Brand serves as ID
        meta_name = "_".join(parts[1:])  # ProductName (may contain underscores)
        return {"meta_id": meta_id, "meta_name": meta_name, "meta_extra": {}}

    # Fallback: use folder name as-is
    return {"meta_id": None, "meta_name": folder_name, "meta_extra": {}}


async def scan_category(category: str) -> list[dict]:
    """
    Walk a category directory and return a list of dicts ready for DB upsert.

    For subfolder categories (avatars, locations, products):
      Iterates subfolders, parses folder names, lists files within each.

    For flat categories (b_rolls, voiceovers, audio_sfx):
      Lists files directly in the category root.
    """
    cat_dir = settings.asset_dirs.get(category)
    if not cat_dir or not cat_dir.exists():
        return []

    results: list[dict] = []

    if category in SUBFOLDER_CATEGORIES:
        # Subfolder-based: iterate directories
        try:
            entries = await aiofiles.os.listdir(str(cat_dir))
        except OSError:
            return []

        for entry_name in sorted(entries):
            entry_path = cat_dir / entry_name
            if not entry_path.is_dir():
                continue

            parsed = parse_folder_name(category, entry_name)

            # List files inside the subfolder
            try:
                files = await aiofiles.os.listdir(str(entry_path))
            except OSError:
                continue

            for fname in sorted(files):
                fpath = entry_path / fname
                if not fpath.is_file():
                    continue

                # Store path relative to project root
                rel_path = str(fpath.relative_to(settings.base_dir))
                try:
                    stat = await aiofiles.os.stat(str(fpath))
                    file_size = stat.st_size
                except OSError:
                    file_size = None

                results.append(
                    {
                        "category": category,
                        "folder_name": entry_name,
                        "file_name": fname,
                        "file_path": rel_path,
                        "meta_id": parsed.get("meta_id"),
                        "meta_name": parsed.get("meta_name"),
                        "meta_extra": parsed.get("meta_extra"),
                        "file_size": file_size,
                    }
                )
    else:
        # Flat category: files directly in directory
        try:
            files = await aiofiles.os.listdir(str(cat_dir))
        except OSError:
            return []

        for fname in sorted(files):
            fpath = cat_dir / fname
            if not fpath.is_file():
                continue

            rel_path = str(fpath.relative_to(settings.base_dir))
            try:
                stat = await aiofiles.os.stat(str(fpath))
                file_size = stat.st_size
            except OSError:
                file_size = None

            results.append(
                {
                    "category": category,
                    "folder_name": None,
                    "file_name": fname,
                    "file_path": rel_path,
                    "meta_id": None,
                    "meta_name": None,
                    "meta_extra": None,
                    "file_size": file_size,
                }
            )

    return results


async def get_latest_video() -> Path | None:
    """
    Find the most recently modified .mp4 file in output/videos/.
    Returns the full Path or None if no videos exist.
    """
    video_dir = settings.output_dir
    if not video_dir.exists():
        return None

    try:
        files = await aiofiles.os.listdir(str(video_dir))
    except OSError:
        return None

    mp4_files: list[tuple[float, Path]] = []
    for fname in files:
        if not fname.lower().endswith(".mp4"):
            continue
        fpath = video_dir / fname
        if fpath.is_file():
            try:
                stat = await aiofiles.os.stat(str(fpath))
                mp4_files.append((stat.st_mtime, fpath))
            except OSError:
                continue

    if not mp4_files:
        return None

    # Sort by modification time, newest first
    mp4_files.sort(key=lambda x: x[0], reverse=True)
    return mp4_files[0][1]


async def create_asset_folder(category: str, folder_name: str) -> Path:
    """
    Create a new subfolder in a category directory.
    Returns the full path to the created folder.
    """
    cat_dir = settings.asset_dirs.get(category)
    if not cat_dir:
        raise ValueError(f"Unknown category: {category}")

    folder_path = cat_dir / folder_name
    await aiofiles.os.makedirs(str(folder_path), exist_ok=True)
    return folder_path
