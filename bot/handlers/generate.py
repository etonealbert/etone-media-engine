"""
Generate handlers: /generate FSM wizard for video production.
Walks the admin through selecting avatar, location, product,
entering a task description, choosing video length, and launching
the OpenClaw agent pipeline.
"""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from bot.config import CATEGORY_LABELS, SUBFOLDER_CATEGORIES
from bot.database import get_asset_by_id, get_assets_by_category, get_unique_folders
from bot.keyboards.callbacks import CancelCB, ConfirmCB, GenerateCB, LengthCB
from bot.keyboards.inline import (
    asset_list_keyboard,
    confirm_keyboard,
    length_picker_keyboard,
)
from bot.services.openclaw import run_openclaw_agents
from bot.states.generate import GenerateStates

logger = logging.getLogger(__name__)

router = Router(name="generate")


# =============================================================================
# /generate - Start the wizard
# =============================================================================


@router.message(Command("generate"))
async def generate_start_handler(message: Message, state: FSMContext) -> None:
    """Start the video generation wizard. Step 1: Select avatar."""
    await state.clear()  # Clear any leftover state

    keyboard = await asset_list_keyboard("avatars", page=0, cb_action="avatar")

    await state.set_state(GenerateStates.select_avatar)
    await message.answer(
        "<b>Video Generation Wizard</b>\n\n"
        "<b>Step 1/5:</b> Select an avatar\n\n"
        "If the list is empty, run /index first to scan your asset library.",
        reply_markup=keyboard,
    )


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
        # Pagination: show the requested page
        keyboard = await asset_list_keyboard("avatars", page=callback_data.page, cb_action="avatar")
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer()
        return

    # Asset selected - look up details
    asset = await get_asset_by_id(callback_data.id)
    if not asset:
        # Fallback: try to find by folder listing
        folders = await get_unique_folders("avatars")
        if folders:
            asset = folders[0]
            asset["id"] = callback_data.id

    if not asset:
        await callback.message.edit_text("Avatar not found in database. Run /index and try again.")
        await state.clear()
        await callback.answer()
        return

    # Store avatar selection
    await state.update_data(
        avatar_id=callback_data.id,
        avatar_folder=asset.get("folder_name", ""),
        avatar_path=asset.get("file_path", ""),
        avatar_name=asset.get("meta_name") or asset.get("folder_name", "Unknown"),
    )

    # Move to Step 2: Location
    await state.set_state(GenerateStates.select_location)
    keyboard = await asset_list_keyboard("locations", page=0, cb_action="location")

    avatar_display = asset.get("meta_name") or asset.get("folder_name", "Unknown")
    await callback.message.edit_text(
        "<b>Video Generation Wizard</b>\n\n"
        f"<b>Step 1:</b> Avatar: <code>{avatar_display}</code> \u2705\n"
        "<b>Step 2/5:</b> Select a location",
        reply_markup=keyboard,
    )
    await callback.answer()


# =============================================================================
# Step 2: Location Selection
# =============================================================================


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
        keyboard = await asset_list_keyboard(
            "locations", page=callback_data.page, cb_action="location"
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
        await callback.message.edit_text(
            "Location not found in database. Run /index and try again."
        )
        await state.clear()
        await callback.answer()
        return

    data = await state.get_data()
    await state.update_data(
        location_id=callback_data.id,
        location_folder=asset.get("folder_name", ""),
        location_path=asset.get("file_path", ""),
        location_name=asset.get("meta_name") or asset.get("folder_name", "Unknown"),
    )

    # Move to Step 3: Product
    await state.set_state(GenerateStates.select_product)
    keyboard = await asset_list_keyboard("products", page=0, cb_action="product")

    avatar_name = data.get("avatar_name", "?")
    location_display = asset.get("meta_name") or asset.get("folder_name", "Unknown")
    await callback.message.edit_text(
        "<b>Video Generation Wizard</b>\n\n"
        f"<b>Step 1:</b> Avatar: <code>{avatar_name}</code> \u2705\n"
        f"<b>Step 2:</b> Location: <code>{location_display}</code> \u2705\n"
        "<b>Step 3/5:</b> Select a product",
        reply_markup=keyboard,
    )
    await callback.answer()


# =============================================================================
# Step 3: Product Selection
# =============================================================================


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
        keyboard = await asset_list_keyboard(
            "products", page=callback_data.page, cb_action="product"
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
        await callback.message.edit_text("Product not found in database. Run /index and try again.")
        await state.clear()
        await callback.answer()
        return

    data = await state.get_data()
    await state.update_data(
        product_id=callback_data.id,
        product_folder=asset.get("folder_name", ""),
        product_path=asset.get("file_path", ""),
        product_name=asset.get("meta_name") or asset.get("folder_name", "Unknown"),
    )

    # Move to Step 4: Task description
    await state.set_state(GenerateStates.enter_task)

    avatar_name = data.get("avatar_name", "?")
    location_name = data.get("location_name", "?")
    product_display = asset.get("meta_name") or asset.get("folder_name", "Unknown")
    await callback.message.edit_text(
        "<b>Video Generation Wizard</b>\n\n"
        f"<b>Step 1:</b> Avatar: <code>{avatar_name}</code> \u2705\n"
        f"<b>Step 2:</b> Location: <code>{location_name}</code> \u2705\n"
        f"<b>Step 3:</b> Product: <code>{product_display}</code> \u2705\n"
        "<b>Step 4/5:</b> Enter task description\n\n"
        "Send an Amazon product link or describe the video task.\n"
        "Example: <i>https://amazon.com/dp/B0... or 'Fitness supplement review targeting gym beginners'</i>"
    )
    await callback.answer()


# =============================================================================
# Step 4: Task Description (Free Text)
# =============================================================================


@router.message(GenerateStates.enter_task, F.text)
async def task_entered_handler(message: Message, state: FSMContext) -> None:
    """Receive the task description or Amazon link."""
    task_text = message.text.strip()

    if not task_text or task_text.startswith("/"):
        await message.answer(
            "Please enter a task description or Amazon link. Send /cancel to abort."
        )
        return

    data = await state.get_data()
    await state.update_data(task_description=task_text)

    # Move to Step 5: Video length
    await state.set_state(GenerateStates.select_length)

    await message.answer(
        "<b>Video Generation Wizard</b>\n\n"
        f"<b>Step 1:</b> Avatar: <code>{data.get('avatar_name', '?')}</code> \u2705\n"
        f"<b>Step 2:</b> Location: <code>{data.get('location_name', '?')}</code> \u2705\n"
        f"<b>Step 3:</b> Product: <code>{data.get('product_name', '?')}</code> \u2705\n"
        f"<b>Step 4:</b> Task: <code>{task_text[:60]}{'...' if len(task_text) > 60 else ''}</code> \u2705\n"
        "<b>Step 5/5:</b> Select target video length\n\n"
        "\u26a0\ufe0f Videos longer than 15s will use Kling chaining (multiple segments).",
        reply_markup=length_picker_keyboard(),
    )


# =============================================================================
# Step 5: Video Length Selection
# =============================================================================


@router.callback_query(GenerateStates.select_length, LengthCB.filter())
async def length_selected_handler(
    callback: CallbackQuery,
    callback_data: LengthCB,
    state: FSMContext,
) -> None:
    """Handle target video length selection."""
    seconds = callback_data.seconds
    data = await state.get_data()
    await state.update_data(target_length_seconds=seconds)

    needs_chaining = seconds > 15
    num_segments = (seconds + 14) // 15

    chaining_note = ""
    if needs_chaining:
        chaining_note = (
            f"\n\n\u26a0\ufe0f <b>Chaining enabled:</b> {num_segments} segments will be "
            f"generated and connected. Last frame of each segment becomes the "
            f"start frame of the next."
        )

    # Move to confirmation
    await state.set_state(GenerateStates.confirm)

    task_text = data.get("task_description", "")
    task_display = task_text[:60] + ("..." if len(task_text) > 60 else "")

    await callback.message.edit_text(
        "<b>Video Generation Wizard - Summary</b>\n\n"
        f"\U0001f9d1 <b>Avatar:</b> <code>{data.get('avatar_name', '?')}</code>\n"
        f"\U0001f3e0 <b>Location:</b> <code>{data.get('location_name', '?')}</code>\n"
        f"\U0001f4e6 <b>Product:</b> <code>{data.get('product_name', '?')}</code>\n"
        f"\U0001f4dd <b>Task:</b> <code>{task_display}</code>\n"
        f"\u23f1 <b>Length:</b> {seconds}s"
        f"{chaining_note}\n\n"
        "Ready to launch?",
        reply_markup=confirm_keyboard(),
    )
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

    # Send initial status message
    status_msg = await callback.message.edit_text(
        "\U0001f3ac <b>Data collected. Launching OpenClaw agents...</b>"
    )

    # Build task data dict
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
        "target_length_seconds": data.get("target_length_seconds", 15),
    }

    # Run the agent pipeline (mock)
    video_path = await run_openclaw_agents(task_data, status_msg, bot)

    # Deliver the video if found
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
    """Cancel the generation at confirmation stage."""
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
        GenerateStates.select_length,
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
