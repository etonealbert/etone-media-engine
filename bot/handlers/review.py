"""
Review handler: /review command to browse and preview assets in the library.
Flow:
  1. Pick a category (all 6)
  2. Pick a folder (subfolder cats) or file (flat cats)
  3. Bot sends the files as a media group (album) or individual file
"""

import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import CATEGORY_LABELS, SUBFOLDER_CATEGORIES, settings
from bot.database import (
    get_asset_by_id,
    get_assets_by_category,
    get_assets_in_folder,
    get_unique_folders,
)
from bot.keyboards.callbacks import CancelCB, ReviewCB
from bot.states.review import ReviewStates
from bot.utils.pagination import paginate

logger = logging.getLogger(__name__)

router = Router(name="review")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"}


# =============================================================================
# Keyboard builders
# =============================================================================


def _review_category_keyboard():
    """Build category picker for /review."""
    builder = InlineKeyboardBuilder()
    for cat_key, label in CATEGORY_LABELS.items():
        builder.button(
            text=label,
            callback_data=ReviewCB(action="cat", cat=cat_key),
        )
    builder.button(text="\u274c Cancel", callback_data=CancelCB())
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def _review_item_keyboard(
    items: list[dict],
    category: str,
    page: int,
    name_key: str,
) -> tuple:
    """Build paginated item list keyboard for review browsing.

    Returns:
        Tuple of (keyboard, has_items).
    """
    has_items = len(items) > 0
    page_items, has_prev, has_next = paginate(items, page, per_page=6)

    builder = InlineKeyboardBuilder()

    for item in page_items:
        name = item.get(name_key, "unknown")
        display = name[:30] + "..." if len(name) > 33 else name
        item_id = item.get("id", 0) or 0

        builder.button(
            text=display,
            callback_data=ReviewCB(action="item", cat=category, id=item_id, page=page),
        )

    # Navigation
    nav_count = 0
    if has_prev:
        builder.button(
            text="\u25c0 Prev",
            callback_data=ReviewCB(action="page", cat=category, page=page - 1),
        )
        nav_count += 1
    if has_next:
        builder.button(
            text="Next \u25b6",
            callback_data=ReviewCB(action="page", cat=category, page=page + 1),
        )
        nav_count += 1

    # Bottom row: Back + Cancel
    builder.button(
        text="\u25c0 Categories",
        callback_data=ReviewCB(action="back"),
    )
    builder.button(text="\u274c Cancel", callback_data=CancelCB())

    rows = [1] * len(page_items)
    if nav_count:
        rows.append(nav_count)
    rows.append(2)  # Back + Cancel
    builder.adjust(*rows)

    return builder.as_markup(), has_items


def _after_view_keyboard(category: str, page: int = 0):
    """Keyboard shown after sending asset preview."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="\u25c0 Back to list",
        callback_data=ReviewCB(action="page", cat=category, page=page),
    )
    builder.button(
        text="\u25c0 Categories",
        callback_data=ReviewCB(action="back"),
    )
    builder.button(text="\u274c Cancel", callback_data=CancelCB())
    builder.adjust(2, 1)
    return builder.as_markup()


# =============================================================================
# /review — Start browsing
# =============================================================================


@router.message(Command("review"))
async def review_start_handler(message: Message, state: FSMContext) -> None:
    """Start the asset review browser."""
    await state.clear()
    await state.set_state(ReviewStates.select_category)
    await message.answer(
        "<b>Asset Library Browser</b>\n\nSelect a category to browse:",
        reply_markup=_review_category_keyboard(),
    )


# =============================================================================
# Category selected
# =============================================================================


@router.callback_query(
    ReviewStates.select_category,
    ReviewCB.filter(F.action == "cat"),
)
async def category_selected_handler(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    state: FSMContext,
) -> None:
    """Handle category selection — show folder or file list."""
    category = callback_data.cat
    await state.update_data(review_category=category)
    await _show_item_list(callback, state, category, page=0)
    await callback.answer()


async def _show_item_list(
    callback: CallbackQuery,
    state: FSMContext,
    category: str,
    page: int,
) -> None:
    """Show paginated folder/file list for the selected category."""
    await state.set_state(ReviewStates.select_item)
    label = CATEGORY_LABELS.get(category, category)

    if category in SUBFOLDER_CATEGORIES:
        items = await get_unique_folders(category)
        name_key = "folder_name"
    else:
        items = await get_assets_by_category(category)
        name_key = "file_name"

    keyboard, has_items = _review_item_keyboard(items, category, page, name_key)

    if has_items:
        item_type = "a folder" if category in SUBFOLDER_CATEGORIES else "a file"
        text = f"<b>Asset Library \u2014 {label}</b>\n\nSelect {item_type} to preview:"
    else:
        text = (
            f"<b>Asset Library \u2014 {label}</b>\n\n"
            f"\u26a0\ufe0f <i>No assets in this category.</i>\n"
            f"You can add assets anytime \u2014 just send a file to the bot "
            f"or use /create_asset."
        )

    await callback.message.edit_text(text, reply_markup=keyboard)


# =============================================================================
# Pagination within item list
# =============================================================================


@router.callback_query(
    ReviewStates.select_item,
    ReviewCB.filter(F.action == "page"),
)
async def page_handler(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    state: FSMContext,
) -> None:
    """Handle pagination in the item list."""
    await _show_item_list(callback, state, callback_data.cat, callback_data.page)
    await callback.answer()


# =============================================================================
# Back to categories
# =============================================================================


@router.callback_query(
    StateFilter(ReviewStates.select_category, ReviewStates.select_item),
    ReviewCB.filter(F.action == "back"),
)
async def back_to_categories_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Go back to category selection."""
    await state.set_state(ReviewStates.select_category)
    await callback.message.edit_text(
        "<b>Asset Library Browser</b>\n\nSelect a category to browse:",
        reply_markup=_review_category_keyboard(),
    )
    await callback.answer()


# =============================================================================
# Item selected — send preview
# =============================================================================


@router.callback_query(
    ReviewStates.select_item,
    ReviewCB.filter(F.action == "item"),
)
async def item_selected_handler(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Handle folder/file selection — send asset preview."""
    category = callback_data.cat
    page = callback_data.page

    if category in SUBFOLDER_CATEGORIES:
        await _send_folder_preview(callback, callback_data, bot, category, page)
    else:
        await _send_file_preview(callback, callback_data, state, bot, category, page)

    await callback.answer()


async def _send_folder_preview(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    bot: Bot,
    category: str,
    page: int,
) -> None:
    """Send all files in a subfolder as a media group (album)."""
    asset = await get_asset_by_id(callback_data.id)
    if not asset or not asset.get("folder_name"):
        await callback.message.edit_text(
            "\u26a0\ufe0f Asset not found. Run /index and try again.",
        )
        return

    folder_name = asset["folder_name"]
    files = await get_assets_in_folder(category, folder_name)

    if not files:
        await callback.message.edit_text(
            f"\u26a0\ufe0f No files found in folder <code>{folder_name}</code>.",
        )
        return

    # Edit the picker message to show what we're viewing
    label = CATEGORY_LABELS.get(category, category)
    display_name = asset.get("meta_name") or folder_name
    await callback.message.edit_text(
        f"<b>{label} \u2014 {display_name}</b>\n\n"
        f"\U0001f4c1 <code>{folder_name}</code>\n"
        f"\U0001f4c4 {len(files)} file(s)",
        reply_markup=_after_view_keyboard(category, page),
    )

    # Separate files by type for proper sending
    media_items = []  # photos + videos (can be grouped in album)
    audio_items = []  # must be sent individually

    for f in files:
        file_path = Path(settings.base_dir) / f["file_path"]
        if not file_path.exists():
            logger.warning("File not found on disk: %s", file_path)
            continue

        ext = file_path.suffix.lower()
        input_file = FSInputFile(str(file_path), filename=file_path.name)

        if ext in IMAGE_EXTENSIONS:
            media_items.append(InputMediaPhoto(media=input_file))
        elif ext in VIDEO_EXTENSIONS:
            media_items.append(InputMediaVideo(media=input_file))
        elif ext in AUDIO_EXTENSIONS:
            audio_items.append((file_path, input_file))
        else:
            audio_items.append((file_path, input_file))

    chat_id = callback.message.chat.id

    # Send media group in chunks of 10 (Telegram limit)
    for i in range(0, len(media_items), 10):
        chunk = media_items[i : i + 10]
        if chunk:
            try:
                await bot.send_media_group(chat_id=chat_id, media=chunk)
            except Exception as e:
                logger.error("Failed to send media group: %s", e)
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"\u26a0\ufe0f Failed to send album: {e}",
                )

    # Send audio/documents individually
    for file_path, input_file in audio_items:
        try:
            ext = file_path.suffix.lower()
            if ext in AUDIO_EXTENSIONS:
                await bot.send_audio(
                    chat_id=chat_id,
                    audio=input_file,
                    title=file_path.stem,
                )
            else:
                await bot.send_document(chat_id=chat_id, document=input_file)
        except Exception as e:
            logger.error("Failed to send file %s: %s", file_path.name, e)

    if not media_items and not audio_items:
        await bot.send_message(
            chat_id=chat_id,
            text="\u26a0\ufe0f All files in this folder are missing from disk. "
            "Run /index to refresh.",
        )


async def _send_file_preview(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    state: FSMContext,
    bot: Bot,
    category: str,
    page: int,
) -> None:
    """Send a single file preview for flat categories."""
    asset = await get_asset_by_id(callback_data.id)
    if not asset:
        await callback.message.edit_text(
            "\u26a0\ufe0f Asset not found. Run /index and try again.",
        )
        await state.clear()
        return

    file_path = Path(settings.base_dir) / asset["file_path"]
    if not file_path.exists():
        await callback.message.edit_text(
            f"\u26a0\ufe0f File not found on disk: <code>{asset['file_name']}</code>\n"
            "Run /index to refresh the database.",
        )
        await state.clear()
        return

    # Edit picker message to show what we're viewing
    label = CATEGORY_LABELS.get(category, category)
    await callback.message.edit_text(
        f"<b>{label} \u2014 {asset['file_name']}</b>",
        reply_markup=_after_view_keyboard(category, page),
    )

    chat_id = callback.message.chat.id
    ext = file_path.suffix.lower()
    input_file = FSInputFile(str(file_path), filename=file_path.name)

    try:
        if ext in IMAGE_EXTENSIONS:
            await bot.send_photo(chat_id=chat_id, photo=input_file)
        elif ext in VIDEO_EXTENSIONS:
            await bot.send_video(chat_id=chat_id, video=input_file)
        elif ext in AUDIO_EXTENSIONS:
            await bot.send_audio(
                chat_id=chat_id,
                audio=input_file,
                title=file_path.stem,
            )
        else:
            await bot.send_document(chat_id=chat_id, document=input_file)
    except Exception as e:
        logger.error("Failed to send file %s: %s", file_path.name, e)
        await bot.send_message(
            chat_id=chat_id,
            text=f"\u26a0\ufe0f Failed to send file: {e}",
        )


# =============================================================================
# Universal Cancel for Review Flow
# =============================================================================


@router.callback_query(
    StateFilter(ReviewStates.select_category, ReviewStates.select_item),
    CancelCB.filter(),
)
async def review_cancel_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Cancel the review browser."""
    await state.clear()
    await callback.message.edit_text("Asset browser closed.")
    await callback.answer()
