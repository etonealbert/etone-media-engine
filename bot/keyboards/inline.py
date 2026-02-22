"""
Inline keyboard builders for the Telegram bot UI.
All keyboards use InlineKeyboardBuilder with CallbackData factories
to stay under the 64-byte callback_data limit.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import CATEGORY_LABELS, SUBFOLDER_CATEGORIES
from bot.database import get_assets_by_category, get_unique_folders
from bot.keyboards.callbacks import (
    AssetCB,
    CancelCB,
    CategoryCB,
    ConfirmCB,
    FolderCB,
    GenerateCB,
    LengthCB,
    ModeCB,
    SkipCB,
)
from bot.utils.pagination import paginate


def category_picker_keyboard() -> InlineKeyboardMarkup:
    """
    Build the category selection keyboard for file uploads.
    Layout: 2 buttons per row, Cancel alone on last row.
    """
    builder = InlineKeyboardBuilder()

    for cat_key, label in CATEGORY_LABELS.items():
        builder.button(
            text=label,
            callback_data=CategoryCB(action="select", name=cat_key),
        )

    builder.button(
        text="\u274c Cancel",
        callback_data=CancelCB(),
    )

    # 2 buttons per row for categories, Cancel alone
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def folder_choice_keyboard(category: str) -> InlineKeyboardMarkup:
    """
    Ask whether to save to a new or existing folder.
    Only used for subfolder-based categories.
    """
    builder = InlineKeyboardBuilder()

    builder.button(
        text="\U0001f4c1 New Folder",
        callback_data=FolderCB(action="new", cat=category),
    )
    builder.button(
        text="\U0001f4c2 Existing Folder",
        callback_data=FolderCB(action="existing", cat=category),
    )
    builder.button(
        text="\u274c Cancel",
        callback_data=CancelCB(),
    )

    builder.adjust(2, 1)
    return builder.as_markup()


async def folder_list_keyboard(
    category: str,
    page: int = 0,
) -> InlineKeyboardMarkup:
    """
    Build a paginated list of existing folders for a subfolder-based category.
    Uses DB queries (not disk reads) for speed.
    """
    folders = await get_unique_folders(category)
    page_items, has_prev, has_next = paginate(folders, page, per_page=6)

    builder = InlineKeyboardBuilder()

    for folder in page_items:
        folder_name = folder.get("folder_name", "unknown")
        display = folder_name[:30] + "..." if len(folder_name) > 33 else folder_name
        builder.button(
            text=display,
            callback_data=FolderCB(
                action="select",
                cat=category,
                id=hash(folder_name) % 100000,
            ),
        )

    # Navigation row
    nav_buttons = []
    if has_prev:
        nav_buttons.append(("< Prev", page - 1))
    if has_next:
        nav_buttons.append(("Next >", page + 1))

    for text, pg in nav_buttons:
        builder.button(
            text=text,
            callback_data=AssetCB(action="page", id=0, page=pg),
        )

    builder.button(
        text="\u274c Cancel",
        callback_data=CancelCB(),
    )

    rows = [1] * len(page_items)
    if nav_buttons:
        rows.append(len(nav_buttons))
    rows.append(1)
    builder.adjust(*rows)

    return builder.as_markup()


async def asset_list_keyboard(
    category: str,
    page: int = 0,
    cb_action: str = "select",
    skip_step: str = "",
) -> tuple[InlineKeyboardMarkup, bool]:
    """
    Build a paginated inline keyboard of assets/folders from the DB.
    For subfolder categories: shows unique folders.
    For flat categories: shows individual files.

    If skip_step is set, a "Skip" button is added so the user can
    proceed without selecting anything from this step.

    Returns:
        Tuple of (keyboard, has_items) — has_items is False when the
        category is empty, so the caller can adjust the message text.
    """
    if category in SUBFOLDER_CATEGORIES:
        items = await get_unique_folders(category)
        name_key = "folder_name"
    else:
        items = await get_assets_by_category(category)
        name_key = "file_name"

    has_items = len(items) > 0
    page_items, has_prev, has_next = paginate(items, page, per_page=6)

    builder = InlineKeyboardBuilder()

    for item in page_items:
        name = item.get(name_key, "unknown")
        display = name[:30] + "..." if len(name) > 33 else name
        item_id = item.get("id", 0) or 0

        builder.button(
            text=display,
            callback_data=GenerateCB(action=cb_action, id=item_id, page=page),
        )

    # Navigation
    nav_count = 0
    if has_prev:
        builder.button(
            text="\u25c0 Prev",
            callback_data=GenerateCB(action=cb_action, id=0, page=page - 1),
        )
        nav_count += 1
    if has_next:
        builder.button(
            text="Next \u25b6",
            callback_data=GenerateCB(action=cb_action, id=0, page=page + 1),
        )
        nav_count += 1

    # Bottom row: Skip + Cancel
    bottom_count = 0
    if skip_step:
        builder.button(
            text="\u23e9 Skip",
            callback_data=SkipCB(step=skip_step),
        )
        bottom_count += 1

    builder.button(
        text="\u274c Cancel",
        callback_data=CancelCB(),
    )
    bottom_count += 1

    rows = [1] * len(page_items)
    if nav_count:
        rows.append(nav_count)
    rows.append(bottom_count)
    builder.adjust(*rows)

    return builder.as_markup(), has_items


def length_picker_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for target video length selection."""
    builder = InlineKeyboardBuilder()

    for seconds in [15, 30, 45, 60]:
        builder.button(
            text=f"{seconds}s",
            callback_data=LengthCB(seconds=seconds),
        )

    builder.button(
        text="\u23e9 Skip (default 15s)",
        callback_data=SkipCB(step="length"),
    )
    builder.button(
        text="\u274c Cancel",
        callback_data=CancelCB(),
    )

    builder.adjust(4, 1, 1)
    return builder.as_markup()


def generation_mode_keyboard() -> InlineKeyboardMarkup:
    """Build mode picker: HITL active, autonomous locked for now."""
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🔒 Fully Autonomous (Coming Soon / Locked)",
        callback_data=ModeCB(action="auto"),
    )
    builder.button(
        text="🧑‍💼 Human-in-the-Loop (HITL)",
        callback_data=ModeCB(action="hitl"),
    )
    builder.button(
        text="❌ Cancel",
        callback_data=CancelCB(),
    )

    builder.adjust(1, 1, 1)
    return builder.as_markup()


def confirm_keyboard() -> InlineKeyboardMarkup:
    """Build confirmation keyboard for launching generation."""
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🚀 Launch",
        callback_data=ConfirmCB(action="launch"),
    )
    builder.button(
        text="❌ Cancel",
        callback_data=ConfirmCB(action="cancel"),
    )

    builder.adjust(2)
    return builder.as_markup()
