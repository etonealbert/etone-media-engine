"""
Generate handlers: /generate FSM wizard for video production.
All steps are OPTIONAL — the user can skip any step and launch
generation with whatever data was provided. Steps:
  1. Avatar (optional)
  2. Location (optional)
  3. Product (optional)
  4. Task description / Amazon link (optional)
  5. Target video length (optional, defaults to 15s)
  6. Creative context / wishes (optional)
  7. Confirm and launch
"""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from bot.database import get_asset_by_id, get_unique_folders
from bot.keyboards.callbacks import CancelCB, ConfirmCB, GenerateCB, LengthCB, SkipCB
from bot.keyboards.inline import (
    asset_list_keyboard,
    confirm_keyboard,
    length_picker_keyboard,
)
from bot.services.openclaw import run_openclaw_agents
from bot.states.generate import GenerateStates

logger = logging.getLogger(__name__)

router = Router(name="generate")

# Default values for skipped steps
SKIP_MARKER = "\u2014"  # em dash, shown in summary for skipped fields
DEFAULT_LENGTH = 15


# =============================================================================
# Helpers
# =============================================================================


def _build_progress(data: dict, current_step: int) -> str:
    """Build the progress header showing completed/skipped steps."""
    lines = []
    steps = [
        ("1", "\U0001f9d1 Avatar", "avatar_name"),
        ("2", "\U0001f3e0 Location", "location_name"),
        ("3", "\U0001f4e6 Product", "product_name"),
        ("4", "\U0001f4dd Task", "task_description"),
        ("5", "\u23f1 Length", "_length_display"),
        ("6", "\U0001f3a8 Context", "creative_context"),
    ]

    for num, label, key in steps:
        step_num = int(num)
        if step_num < current_step:
            if key == "_length_display":
                val = f"{data.get('target_length_seconds', DEFAULT_LENGTH)}s"
            else:
                val = data.get(key, "")

            if val and val != SKIP_MARKER:
                display = val[:40] + "..." if len(str(val)) > 40 else val
                lines.append(f"<b>Step {num}:</b> {label}: <code>{display}</code> \u2705")
            else:
                lines.append(f"<b>Step {num}:</b> {label}: <i>skipped</i> \u23e9")

    return "\n".join(lines)


# =============================================================================
# /generate — Start the wizard
# =============================================================================


@router.message(Command("generate"))
async def generate_start_handler(message: Message, state: FSMContext) -> None:
    """Start the video generation wizard. Step 1: Select avatar."""
    await state.clear()

    keyboard, has_items = await asset_list_keyboard(
        "avatars", page=0, cb_action="avatar", skip_step="avatar"
    )

    await state.set_state(GenerateStates.select_avatar)

    if has_items:
        text = (
            "<b>Video Generation Wizard</b>\n\n"
            "<b>Step 1/6:</b> Select an avatar\n"
            "<i>All steps are optional — press Skip to proceed without selecting.</i>"
        )
    else:
        text = (
            "<b>Video Generation Wizard</b>\n\n"
            "<b>Step 1/6:</b> Select an avatar\n\n"
            "⚠️ <i>No avatars in library.</i>\n"
            "You can add assets anytime — just send a file to the bot "
            "or use /create_asset.\n\n"
            "Press Skip to continue without an avatar."
        )

    await message.answer(text, reply_markup=keyboard)


# =============================================================================
# Step 1: Avatar Selection
# =============================================================================


@router.callback_query(
    GenerateStates.select_avatar,
    GenerateCB.filter(F.action == "avatar"),
)
async def avatar_selected_handler(
    callback: CallbackQuery,
    callback_data: GenerateCB,
    state: FSMContext,
) -> None:
    """Handle avatar selection or pagination."""
    if callback_data.id == 0:
        keyboard, _ = await asset_list_keyboard(
            "avatars", page=callback_data.page, cb_action="avatar", skip_step="avatar"
        )
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer()
        return

    asset = await get_asset_by_id(callback_data.id)
    if not asset:
        folders = await get_unique_folders("avatars")
        if folders:
            asset = folders[0]
            asset["id"] = callback_data.id

    if not asset:
        await callback.message.edit_text("Avatar not found. Run /index and try again.")
        await state.clear()
        await callback.answer()
        return

    await state.update_data(
        avatar_id=callback_data.id,
        avatar_folder=asset.get("folder_name", ""),
        avatar_path=asset.get("file_path", ""),
        avatar_name=asset.get("meta_name") or asset.get("folder_name", "Unknown"),
    )

    await _go_to_step_2(callback, state)
    await callback.answer()


# =============================================================================
# Step 2: Location Selection
# =============================================================================


async def _go_to_step_2(callback: CallbackQuery, state: FSMContext) -> None:
    """Transition to Step 2: Location."""
    data = await state.get_data()
    await state.set_state(GenerateStates.select_location)
    keyboard, has_items = await asset_list_keyboard(
        "locations", page=0, cb_action="location", skip_step="location"
    )

    progress = _build_progress(data, 2)
    if has_items:
        step_text = "<b>Step 2/6:</b> Select a location"
    else:
        step_text = (
            "<b>Step 2/6:</b> Select a location\n\n"
            "⚠️ <i>No locations in library.</i>\n"
            "You can add assets anytime — just send a file to the bot "
            "or use /create_asset.\n\n"
            "Press Skip to continue without a location."
        )
    await callback.message.edit_text(
        f"<b>Video Generation Wizard</b>\n\n{progress}\n{step_text}",
        reply_markup=keyboard,
    )


@router.callback_query(
    GenerateStates.select_location,
    GenerateCB.filter(F.action == "location"),
)
async def location_selected_handler(
    callback: CallbackQuery,
    callback_data: GenerateCB,
    state: FSMContext,
) -> None:
    """Handle location selection or pagination."""
    if callback_data.id == 0:
        keyboard, _ = await asset_list_keyboard(
            "locations", page=callback_data.page, cb_action="location", skip_step="location"
        )
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer()
        return

    asset = await get_asset_by_id(callback_data.id)
    if not asset:
        folders = await get_unique_folders("locations")
        if folders:
            asset = folders[0]
            asset["id"] = callback_data.id

    if not asset:
        await callback.message.edit_text("Location not found. Run /index and try again.")
        await state.clear()
        await callback.answer()
        return

    await state.update_data(
        location_id=callback_data.id,
        location_folder=asset.get("folder_name", ""),
        location_path=asset.get("file_path", ""),
        location_name=asset.get("meta_name") or asset.get("folder_name", "Unknown"),
    )

    await _go_to_step_3(callback, state)
    await callback.answer()


# =============================================================================
# Step 3: Product Selection
# =============================================================================


async def _go_to_step_3(callback: CallbackQuery, state: FSMContext) -> None:
    """Transition to Step 3: Product."""
    data = await state.get_data()
    await state.set_state(GenerateStates.select_product)
    keyboard, has_items = await asset_list_keyboard(
        "products", page=0, cb_action="product", skip_step="product"
    )

    progress = _build_progress(data, 3)
    if has_items:
        step_text = "<b>Step 3/6:</b> Select a product"
    else:
        step_text = (
            "<b>Step 3/6:</b> Select a product\n\n"
            "⚠️ <i>No products in library.</i>\n"
            "You can add assets anytime — just send a file to the bot "
            "or use /create_asset.\n\n"
            "Press Skip to continue without a product."
        )
    await callback.message.edit_text(
        f"<b>Video Generation Wizard</b>\n\n{progress}\n{step_text}",
        reply_markup=keyboard,
    )


@router.callback_query(
    GenerateStates.select_product,
    GenerateCB.filter(F.action == "product"),
)
async def product_selected_handler(
    callback: CallbackQuery,
    callback_data: GenerateCB,
    state: FSMContext,
) -> None:
    """Handle product selection or pagination."""
    if callback_data.id == 0:
        keyboard, _ = await asset_list_keyboard(
            "products", page=callback_data.page, cb_action="product", skip_step="product"
        )
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer()
        return

    asset = await get_asset_by_id(callback_data.id)
    if not asset:
        folders = await get_unique_folders("products")
        if folders:
            asset = folders[0]
            asset["id"] = callback_data.id

    if not asset:
        await callback.message.edit_text("Product not found. Run /index and try again.")
        await state.clear()
        await callback.answer()
        return

    await state.update_data(
        product_id=callback_data.id,
        product_folder=asset.get("folder_name", ""),
        product_path=asset.get("file_path", ""),
        product_name=asset.get("meta_name") or asset.get("folder_name", "Unknown"),
    )

    await _go_to_step_4(callback, state)
    await callback.answer()


# =============================================================================
# Step 4: Task Description (Free Text — optional)
# =============================================================================


async def _go_to_step_4(callback: CallbackQuery, state: FSMContext) -> None:
    """Transition to Step 4: Task description."""
    data = await state.get_data()
    await state.set_state(GenerateStates.enter_task)

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text="\u23e9 Skip", callback_data=SkipCB(step="task"))
    builder.button(text="\u274c Cancel", callback_data=CancelCB())
    builder.adjust(2)

    progress = _build_progress(data, 4)
    await callback.message.edit_text(
        f"<b>Video Generation Wizard</b>\n\n{progress}\n"
        "<b>Step 4/6:</b> Enter task description\n\n"
        "Send an Amazon product link or describe the video task.\n"
        "Or press Skip to proceed without a task description.",
        reply_markup=builder.as_markup(),
    )


@router.message(GenerateStates.enter_task, F.text)
async def task_entered_handler(message: Message, state: FSMContext) -> None:
    """Receive the task description or Amazon link."""
    task_text = message.text.strip()

    if not task_text or task_text.startswith("/"):
        await message.answer(
            "Please enter a task description, Amazon link, or press Skip. Send /cancel to abort."
        )
        return

    await state.update_data(task_description=task_text)
    data = await state.get_data()

    # Move to Step 5
    await state.set_state(GenerateStates.select_length)

    progress = _build_progress(data, 5)
    await message.answer(
        f"<b>Video Generation Wizard</b>\n\n{progress}\n"
        "<b>Step 5/6:</b> Select target video length\n\n"
        "\u26a0\ufe0f Videos longer than 15s will use Kling chaining.",
        reply_markup=length_picker_keyboard(),
    )


# =============================================================================
# Step 5: Video Length Selection (optional — defaults to 15s)
# =============================================================================


async def _go_to_step_5(callback: CallbackQuery, state: FSMContext) -> None:
    """Transition to Step 5: Length."""
    data = await state.get_data()
    await state.set_state(GenerateStates.select_length)

    progress = _build_progress(data, 5)
    await callback.message.edit_text(
        f"<b>Video Generation Wizard</b>\n\n{progress}\n"
        "<b>Step 5/6:</b> Select target video length\n\n"
        "\u26a0\ufe0f Videos longer than 15s will use Kling chaining.",
        reply_markup=length_picker_keyboard(),
    )


@router.callback_query(GenerateStates.select_length, LengthCB.filter())
async def length_selected_handler(
    callback: CallbackQuery,
    callback_data: LengthCB,
    state: FSMContext,
) -> None:
    """Handle target video length selection."""
    await state.update_data(target_length_seconds=callback_data.seconds)
    await _go_to_step_6(callback, state)
    await callback.answer()


# =============================================================================
# Step 6: Creative Context / Wishes (Free Text — optional)
# =============================================================================


async def _go_to_step_6(callback: CallbackQuery, state: FSMContext) -> None:
    """Transition to Step 6: Creative context."""
    data = await state.get_data()
    await state.set_state(GenerateStates.enter_context)

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text="\u23e9 Skip", callback_data=SkipCB(step="context"))
    builder.button(text="\u274c Cancel", callback_data=CancelCB())
    builder.adjust(2)

    progress = _build_progress(data, 6)
    await callback.message.edit_text(
        f"<b>Video Generation Wizard</b>\n\n{progress}\n"
        "<b>Step 6/6:</b> Creative context (optional)\n\n"
        "Describe any creative wishes for the video:\n"
        "  - specific dance or movement\n"
        "  - sound effects or music style\n"
        "  - mood, vibe, visual effects\n"
        "  - specific CTA or ending\n\n"
        "Send your wishes as text, or press Skip.",
        reply_markup=builder.as_markup(),
    )


@router.message(GenerateStates.enter_context, F.text)
async def context_entered_handler(message: Message, state: FSMContext) -> None:
    """Receive creative context/wishes."""
    context_text = message.text.strip()

    if not context_text or context_text.startswith("/"):
        await message.answer(
            "Please enter your creative wishes, or press Skip. Send /cancel to abort."
        )
        return

    await state.update_data(creative_context=context_text)
    data = await state.get_data()

    # Move to confirmation
    await _show_confirm(message, state, data)


async def _show_confirm(
    source: Message | CallbackQuery,
    state: FSMContext,
    data: dict,
) -> None:
    """Show the final summary and confirm keyboard."""
    await state.set_state(GenerateStates.confirm)

    seconds = data.get("target_length_seconds", DEFAULT_LENGTH)
    needs_chaining = seconds > 15
    num_segments = (seconds + 14) // 15

    chaining_note = ""
    if needs_chaining:
        chaining_note = f"\n\n\u26a0\ufe0f <b>Chaining:</b> {num_segments} segments"

    def _field(label: str, key: str, icon: str) -> str:
        val = data.get(key, "")
        if val:
            display = str(val)[:50] + "..." if len(str(val)) > 50 else str(val)
            return f"{icon} <b>{label}:</b> <code>{display}</code>"
        return f"{icon} <b>{label}:</b> <i>skipped</i>"

    summary = "\n".join(
        [
            _field("Avatar", "avatar_name", "\U0001f9d1"),
            _field("Location", "location_name", "\U0001f3e0"),
            _field("Product", "product_name", "\U0001f4e6"),
            _field("Task", "task_description", "\U0001f4dd"),
            f"\u23f1 <b>Length:</b> {seconds}s",
            _field("Context", "creative_context", "\U0001f3a8"),
        ]
    )

    text = (
        f"<b>Video Generation Wizard \u2014 Summary</b>\n\n"
        f"{summary}"
        f"{chaining_note}\n\n"
        f"Ready to launch?"
    )

    if isinstance(source, CallbackQuery):
        await source.message.edit_text(text, reply_markup=confirm_keyboard())
    else:
        await source.answer(text, reply_markup=confirm_keyboard())


# =============================================================================
# Skip Handler — routes to the correct next step
# =============================================================================


@router.callback_query(
    StateFilter(
        GenerateStates.select_avatar,
        GenerateStates.select_location,
        GenerateStates.select_product,
        GenerateStates.enter_task,
        GenerateStates.select_length,
        GenerateStates.enter_context,
    ),
    SkipCB.filter(),
)
async def skip_step_handler(
    callback: CallbackQuery,
    callback_data: SkipCB,
    state: FSMContext,
) -> None:
    """Handle skipping any wizard step. Routes to the next step."""
    step = callback_data.step

    if step == "avatar":
        # Skip avatar -> go to location
        await _go_to_step_2(callback, state)
    elif step == "location":
        # Skip location -> go to product
        await _go_to_step_3(callback, state)
    elif step == "product":
        # Skip product -> go to task
        await _go_to_step_4(callback, state)
    elif step == "task":
        # Skip task -> go to length
        await _go_to_step_5(callback, state)
    elif step == "length":
        # Skip length -> default 15s, go to context
        await state.update_data(target_length_seconds=DEFAULT_LENGTH)
        await _go_to_step_6(callback, state)
    elif step == "context":
        # Skip context -> go to confirm
        data = await state.get_data()
        await _show_confirm(callback, state, data)

    await callback.answer()


# =============================================================================
# Confirmation: Launch or Cancel
# =============================================================================


@router.callback_query(GenerateStates.confirm, ConfirmCB.filter(F.action == "launch"))
async def launch_handler(
    callback: CallbackQuery,
    callback_data: ConfirmCB,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Launch the OpenClaw agent pipeline."""
    data = await state.get_data()
    await state.clear()

    status_msg = await callback.message.edit_text("\U0001f3ac <b>Launching OpenClaw agents...</b>")

    task_data = {
        "avatar_folder": data.get("avatar_folder", ""),
        "avatar_path": data.get("avatar_path", ""),
        "avatar_name": data.get("avatar_name", ""),
        "location_folder": data.get("location_folder", ""),
        "location_path": data.get("location_path", ""),
        "location_name": data.get("location_name", ""),
        "product_folder": data.get("product_folder", ""),
        "product_path": data.get("product_path", ""),
        "product_name": data.get("product_name", ""),
        "task_description": data.get("task_description", ""),
        "target_length_seconds": data.get("target_length_seconds", DEFAULT_LENGTH),
        "creative_context": data.get("creative_context", ""),
    }

    video_path = await run_openclaw_agents(task_data, status_msg, bot)

    if video_path and video_path.exists():
        try:
            video_file = FSInputFile(str(video_path), filename=video_path.name)
            await bot.send_video(
                chat_id=callback.message.chat.id,
                video=video_file,
                caption="\U0001f525 Your video is ready!",
            )
        except Exception as e:
            logger.error(f"Failed to send video: {e}")
            await bot.send_message(
                chat_id=callback.message.chat.id,
                text=f"\u26a0\ufe0f Video generated but failed to send: {e}\n"
                f"File: <code>{video_path}</code>",
            )
    else:
        await bot.send_message(
            chat_id=callback.message.chat.id,
            text=(
                "\U0001f3ac Pipeline finished!\n\n"
                "No output video found. To test delivery:\n"
                "1. Place a .mp4 file in <code>output/videos/</code>\n"
                "2. Run /generate again"
            ),
        )

    await callback.answer()


@router.callback_query(GenerateStates.confirm, ConfirmCB.filter(F.action == "cancel"))
async def generation_cancel_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Cancel at confirmation stage."""
    await state.clear()
    await callback.message.edit_text("Video generation cancelled.")
    await callback.answer()


# =============================================================================
# Universal Cancel for Generate Flow
# =============================================================================


@router.callback_query(
    StateFilter(
        GenerateStates.select_avatar,
        GenerateStates.select_location,
        GenerateStates.select_product,
        GenerateStates.enter_task,
        GenerateStates.select_length,
        GenerateStates.enter_context,
        GenerateStates.confirm,
    ),
    CancelCB.filter(),
)
async def generate_cancel_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Cancel the generation wizard at any step."""
    await state.clear()
    await callback.message.edit_text("Video generation wizard cancelled.")
    await callback.answer()
