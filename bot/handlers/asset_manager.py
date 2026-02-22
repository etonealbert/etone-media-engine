"""
Asset manager handlers: file upload catch-all, /index, /create_asset.
Handles the complete upload flow including subfolder routing for
structured categories (avatars, locations, products).
"""

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import CATEGORIES, CATEGORY_LABELS, SUBFOLDER_CATEGORIES, settings
from bot.database import (
    clear_category,
    count_assets,
    get_next_meta_id,
    get_unique_folders,
    upsert_asset,
)
from bot.keyboards.callbacks import CancelCB, CategoryCB, FolderCB
from bot.keyboards.inline import (
    category_picker_keyboard,
    folder_choice_keyboard,
    folder_list_keyboard,
)
from bot.services.file_manager import (
    create_asset_folder,
    generate_save_path,
    parse_folder_name,
    scan_category,
)
from bot.states.upload import UploadStates

logger = logging.getLogger(__name__)

router = Router(name="asset_manager")


# =============================================================================
# File Upload Catch-All
# =============================================================================


@router.message(StateFilter(None), F.photo | F.document | F.video | F.audio)
async def media_intercept_handler(message: Message, state: FSMContext) -> None:
    """
    Catch-all handler for any media sent without a command.
    Extracts file_id and original filename, stores in FSM,
    then presents the category picker keyboard.
    """
    file_id: str = ""
    original_name: str = "unnamed_file"

    if message.photo:
        # Select highest resolution (last in the list)
        photo = message.photo[-1]
        file_id = photo.file_id
        original_name = f"photo_{photo.file_unique_id}.jpg"
    elif message.document:
        file_id = message.document.file_id
        original_name = message.document.file_name or "unnamed_document"
    elif message.video:
        file_id = message.video.file_id
        original_name = message.video.file_name or f"video_{message.video.file_unique_id}.mp4"
    elif message.audio:
        file_id = message.audio.file_id
        original_name = message.audio.file_name or f"audio_{message.audio.file_unique_id}.mp3"

    # Store file info in FSM for use after category selection
    await state.update_data(
        upload_file_id=file_id,
        upload_original_name=original_name,
    )
    await state.set_state(UploadStates.waiting_category)

    await message.answer(
        f"<b>File received:</b> <code>{original_name}</code>\n\nWhere should I save this file?",
        reply_markup=category_picker_keyboard(),
    )


# =============================================================================
# Category Selection Callback
# =============================================================================


@router.callback_query(UploadStates.waiting_category, CategoryCB.filter(F.action == "select"))
async def category_selected_handler(
    callback: CallbackQuery,
    callback_data: CategoryCB,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Handle category selection from the picker keyboard."""
    category = callback_data.name
    data = await state.get_data()
    file_id = data.get("upload_file_id")
    original_name = data.get("upload_original_name", "unnamed_file")

    if not file_id:
        await callback.message.edit_text("Error: file data lost. Please resend the file.")
        await state.clear()
        await callback.answer()
        return

    # Store selected category
    await state.update_data(upload_category=category)

    if category in SUBFOLDER_CATEGORIES:
        # Ask whether to create a new folder or use existing
        await state.set_state(UploadStates.waiting_folder_choice)
        label = CATEGORY_LABELS.get(category, category)
        await callback.message.edit_text(
            f"<b>Category:</b> {label}\n"
            f"<b>File:</b> <code>{original_name}</code>\n\n"
            "Save to a new folder or existing one?",
            reply_markup=folder_choice_keyboard(category),
        )
    else:
        # Flat category: download immediately
        await _download_and_save(callback, state, bot, category, None)

    await callback.answer()


# =============================================================================
# Folder Choice: New or Existing
# =============================================================================


@router.callback_query(UploadStates.waiting_folder_choice, FolderCB.filter(F.action == "new"))
async def new_folder_handler(
    callback: CallbackQuery,
    callback_data: FolderCB,
    state: FSMContext,
) -> None:
    """User chose to create a new folder."""
    category = callback_data.cat
    await state.update_data(upload_category=category)
    await state.set_state(UploadStates.waiting_folder_name)

    # Provide naming convention hint
    hints = {
        "avatars": "<code>[ID]_[Name]_[Gender]_[Age]_[Niche]</code>\nExample: <code>AV01_Sarah_F_25_Fitness</code>",
        "locations": "<code>[ID]_[Environment]_[Lighting]</code>\nExample: <code>LOC01_ModernKitchen_MorningSun</code>",
        "products": "<code>[Brand]_[ProductName]</code>\nExample: <code>Ghost_Creatine</code>",
    }
    hint = hints.get(category, "Enter folder name:")

    await callback.message.edit_text(
        f"<b>Enter folder name:</b>\n\nNaming convention:\n{hint}\n\n"
        "Send the folder name as a text message, or /cancel to abort."
    )
    await callback.answer()


@router.callback_query(UploadStates.waiting_folder_choice, FolderCB.filter(F.action == "existing"))
async def existing_folder_handler(
    callback: CallbackQuery,
    callback_data: FolderCB,
    state: FSMContext,
) -> None:
    """User chose to save to an existing folder."""
    category = callback_data.cat
    await state.update_data(upload_category=category)

    folders = await get_unique_folders(category)
    if not folders:
        await callback.message.edit_text(
            f"No existing folders found in <b>{category}</b>.\n"
            "Run /index first or choose 'New Folder'.",
        )
        await state.clear()
        await callback.answer()
        return

    # Store folder list for selection
    folder_names = [f.get("folder_name", "") for f in folders if f.get("folder_name")]
    await state.update_data(upload_folder_list=folder_names)
    await state.set_state(UploadStates.waiting_folder_select)

    await callback.message.edit_text(
        f"<b>Select folder in {CATEGORY_LABELS.get(category, category)}:</b>",
        reply_markup=await folder_list_keyboard(category, page=0),
    )
    await callback.answer()


# =============================================================================
# Folder Name Input (New Folder)
# =============================================================================


@router.message(UploadStates.waiting_folder_name, F.text)
async def folder_name_received_handler(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Receive the folder name for a new subfolder."""
    folder_name = message.text.strip()

    if not folder_name or folder_name.startswith("/"):
        await message.answer("Invalid folder name. Please try again or /cancel.")
        return

    data = await state.get_data()
    category = data.get("upload_category", "")

    # Create the folder
    try:
        await create_asset_folder(category, folder_name)
    except Exception as e:
        await message.answer(f"Error creating folder: {e}")
        await state.clear()
        return

    await state.update_data(upload_folder_name=folder_name)

    # Download and save
    file_id = data.get("upload_file_id")
    original_name = data.get("upload_original_name", "unnamed_file")

    cat_dir = settings.asset_dirs.get(category)
    if not cat_dir or not file_id:
        await message.answer("Error: missing file data. Please resend.")
        await state.clear()
        return

    folder_path = cat_dir / folder_name
    save_path = await generate_save_path(folder_path, original_name)

    await bot.download(file_id, destination=str(save_path))

    # Auto re-index this category
    await _index_single_category(category)

    label = CATEGORY_LABELS.get(category, category)
    await message.answer(
        f"\u2705 File saved!\n\n"
        f"<b>Category:</b> {label}\n"
        f"<b>Folder:</b> <code>{folder_name}</code>\n"
        f"<b>File:</b> <code>{save_path.name}</code>"
    )
    await state.clear()


# =============================================================================
# Folder Selection (Existing Folder)
# =============================================================================


@router.callback_query(UploadStates.waiting_folder_select, FolderCB.filter(F.action == "select"))
async def folder_selected_handler(
    callback: CallbackQuery,
    callback_data: FolderCB,
    state: FSMContext,
    bot: Bot,
) -> None:
    """User selected an existing folder from the list."""
    data = await state.get_data()
    category = data.get("upload_category", callback_data.cat)
    folder_list = data.get("upload_folder_list", [])

    # Find the folder by hash match
    target_hash = callback_data.id
    folder_name = None
    for fname in folder_list:
        if hash(fname) % 100000 == target_hash:
            folder_name = fname
            break

    if not folder_name:
        # Fallback: use first folder
        folder_name = folder_list[0] if folder_list else None

    if not folder_name:
        await callback.message.edit_text("Error: folder not found.")
        await state.clear()
        await callback.answer()
        return

    await _download_and_save(callback, state, bot, category, folder_name)
    await callback.answer()


# =============================================================================
# Cancel Callback (for upload flow)
# =============================================================================


@router.callback_query(
    StateFilter(
        UploadStates.waiting_category,
        UploadStates.waiting_folder_choice,
        UploadStates.waiting_folder_select,
    ),
    CancelCB.filter(),
)
async def upload_cancel_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel the upload flow."""
    await state.clear()
    await callback.message.edit_text("Upload cancelled.")
    await callback.answer()


# =============================================================================
# /index Command
# =============================================================================


@router.message(Command("index"))
async def index_handler(message: Message) -> None:
    """Scan all asset directories and update the SQLite index."""
    status_msg = await message.answer("\U0001f504 Indexing asset library...")

    total = 0
    report_lines = []

    for category in CATEGORIES:
        # Clear existing entries for this category
        await clear_category(category)

        # Scan filesystem
        items = await scan_category(category)

        # Upsert into database
        for item in items:
            await upsert_asset(**item)

        count = len(items)
        total += count
        label = CATEGORY_LABELS.get(category, category)
        report_lines.append(f"  {label}: <b>{count}</b> files")

    report = "\n".join(report_lines)
    await status_msg.edit_text(
        f"\u2705 <b>Index complete!</b>\n\n{report}\n\n<b>Total:</b> {total} assets indexed"
    )


# =============================================================================
# /create_asset Command
# =============================================================================


@router.message(Command("create_asset"))
async def create_asset_handler(message: Message) -> None:
    """
    Create a new asset folder with structured naming.
    Usage: /create_asset [type] [description]
    Example: /create_asset avatar A 25yo fitness female
    """
    # Parse command arguments
    args = message.text.split(maxsplit=2) if message.text else []

    if len(args) < 3:
        await message.answer(
            "<b>Usage:</b> <code>/create_asset [type] [description]</code>\n\n"
            "<b>Types:</b> avatar, location, product, b_roll, voiceover, sfx\n\n"
            "<b>Examples:</b>\n"
            "<code>/create_asset avatar A 25yo fitness female</code>\n"
            "<code>/create_asset location Modern kitchen with morning sun</code>\n"
            "<code>/create_asset product Ghost Creatine</code>"
        )
        return

    asset_type = args[1].lower()
    description = args[2]

    # Map shorthand to category names
    type_map = {
        "avatar": "avatars",
        "location": "locations",
        "product": "products",
        "b_roll": "b_rolls",
        "voiceover": "voiceovers",
        "sfx": "audio_sfx",
    }

    category = type_map.get(asset_type)
    if not category:
        await message.answer(
            f"Unknown asset type: <code>{asset_type}</code>\n"
            f"Valid types: {', '.join(type_map.keys())}"
        )
        return

    if category not in SUBFOLDER_CATEGORIES:
        await message.answer(
            f"<code>{asset_type}</code> uses flat storage. "
            "Simply drop the file into the chat to save it."
        )
        return

    # Generate folder name based on category conventions
    status_msg = await message.answer(
        f"\U0001f4c1 Creating {asset_type} asset...\nDescription: <i>{description}</i>"
    )

    folder_name = await _generate_folder_name(category, description)

    # Create the folder
    try:
        folder_path = await create_asset_folder(category, folder_name)
    except Exception as e:
        await status_msg.edit_text(f"Error creating folder: {e}")
        return

    # Mock OpenClaw agent call for asset generation
    await status_msg.edit_text(
        f"\U0001f3a8 Simulating OpenClaw asset generation...\nFolder: <code>{folder_name}</code>"
    )

    # In production, this would call the OpenClaw Gateway to generate images:
    # POST /api/v1/generate-asset
    # {
    #     "type": asset_type,
    #     "description": description,
    #     "output_folder": str(folder_path),
    #     "required_files": ["front_medium.png", "profile.png"],
    # }
    await asyncio.sleep(2)  # Simulate generation time

    # Create placeholder files to show the folder structure
    required_files = {
        "avatars": ["front_medium.png", "profile_left.png", "profile_right.png", "full_body.png"],
        "locations": ["wide_shot.png", "medium_shot.png", "close_up.png"],
        "products": ["product_front.png", "product_side.png", "label_flat.jpg"],
    }

    placeholders_created = []
    for fname in required_files.get(category, []):
        placeholder = folder_path / fname
        if not placeholder.exists():
            placeholder.write_text(f"[PLACEHOLDER - Replace with actual {fname}]")
            placeholders_created.append(fname)

    # Auto-index
    await _index_single_category(category)

    files_list = "\n".join(f"  - <code>{f}</code>" for f in placeholders_created)
    await status_msg.edit_text(
        f"\u2705 <b>Asset folder created!</b>\n\n"
        f"<b>Category:</b> {CATEGORY_LABELS.get(category, category)}\n"
        f"<b>Folder:</b> <code>{folder_name}</code>\n"
        f"<b>Path:</b> <code>{folder_path}</code>\n\n"
        f"<b>Placeholder files created:</b>\n{files_list}\n\n"
        f"Replace placeholders with actual files by dropping them in the chat."
    )


# =============================================================================
# Private Helpers
# =============================================================================


async def _download_and_save(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    category: str,
    folder_name: str | None,
) -> None:
    """Download the file from Telegram and save to the target location."""
    data = await state.get_data()
    file_id = data.get("upload_file_id")
    original_name = data.get("upload_original_name", "unnamed_file")

    if not file_id:
        await callback.message.edit_text("Error: file data lost. Please resend.")
        await state.clear()
        return

    cat_dir = settings.asset_dirs.get(category)
    if not cat_dir:
        await callback.message.edit_text(f"Error: unknown category '{category}'.")
        await state.clear()
        return

    # Determine target directory
    if folder_name:
        target_dir = cat_dir / folder_name
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = cat_dir
        target_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename with timestamp
    save_path = await generate_save_path(target_dir, original_name)

    # Download file from Telegram
    try:
        await bot.download(file_id, destination=str(save_path))
    except Exception as e:
        await callback.message.edit_text(f"Error downloading file: {e}")
        await state.clear()
        return

    # Auto re-index
    await _index_single_category(category)

    label = CATEGORY_LABELS.get(category, category)
    folder_info = f"\n<b>Folder:</b> <code>{folder_name}</code>" if folder_name else ""
    await callback.message.edit_text(
        f"\u2705 File saved!\n\n"
        f"<b>Category:</b> {label}{folder_info}\n"
        f"<b>File:</b> <code>{save_path.name}</code>"
    )
    await state.clear()


async def _index_single_category(category: str) -> int:
    """Re-index a single category. Returns count of indexed items."""
    await clear_category(category)
    items = await scan_category(category)
    for item in items:
        await upsert_asset(**item)
    return len(items)


async def _generate_folder_name(category: str, description: str) -> str:
    """Generate a properly formatted folder name from a description."""
    # Determine prefix based on category
    prefixes = {
        "avatars": "AV",
        "locations": "LOC",
        "products": "",
    }
    prefix = prefixes.get(category, "")

    if prefix:
        next_id = await get_next_meta_id(category, prefix)
    else:
        next_id = ""

    # Parse description into folder name components
    words = description.strip().split()

    if category == "avatars":
        # Try to extract: Name, Gender, Age, Niche
        name = words[0] if words else "Unknown"
        gender = "U"  # Unknown
        age = "00"
        niche = "General"

        for w in words:
            if w.upper() in ("M", "F", "NB"):
                gender = w.upper()
            elif w.replace("yo", "").isdigit():
                age = w.replace("yo", "")
            elif w.isdigit() and int(w) < 100:
                age = w

        # Last word(s) as niche if not already captured
        remaining = [w for w in words if w.upper() not in (name.upper(), gender, age, f"{age}yo")]
        if remaining:
            niche = "_".join(remaining).title()

        return f"{next_id}_{name}_{gender}_{age}_{niche}"

    elif category == "locations":
        environment = "_".join(words[:2]) if len(words) >= 2 else words[0] if words else "Unknown"
        lighting = "_".join(words[2:]) if len(words) > 2 else "NaturalLight"
        return f"{next_id}_{environment}_{lighting}"

    elif category == "products":
        brand = words[0] if words else "Unknown"
        product_name = "_".join(words[1:]) if len(words) > 1 else "Product"
        return f"{brand}_{product_name}"

    return "_".join(words) if words else "unnamed"
