"""
Common handlers: /start, /help, /cancel.
The cancel handler is registered with StateFilter("*") to intercept
cancel commands from any active FSM wizard.
"""

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

router = Router(name="common")


@router.message(StateFilter("*"), Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    """Global cancel: works from any FSM state."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("No active operation to cancel.")
        return

    await state.clear()
    await message.answer(
        "Operation cancelled. All progress has been reset.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("start"))
async def start_handler(message: Message) -> None:
    """Welcome message with available commands."""
    await message.answer(
        "<b>etone-media-engine</b>\n"
        "AI Video Production Control Center\n\n"
        "<b>Available commands:</b>\n"
        "/index - Scan and index asset library\n"
        "/create_asset - Create new asset folder\n"
        "/generate - Launch video generation wizard\n"
        "/cancel - Cancel current operation\n"
        "/help - Show detailed help\n\n"
        "You can also send any file (photo, video, audio, document) "
        "to save it to the asset library."
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    """Detailed help text explaining all commands and asset rules."""
    await message.answer(
        "<b>Asset Management</b>\n\n"
        "Drop any file into the chat and I'll ask where to save it.\n\n"
        "<b>Asset Categories:</b>\n"
        "  <b>Avatars</b> - Subfolder per avatar: <code>[ID]_[Name]_[Gender]_[Age]_[Niche]</code>\n"
        "    Required: front_medium.png, profile.png\n"
        "  <b>Locations</b> - Subfolder: <code>[ID]_[Environment]_[Lighting]</code>\n"
        "    Required: wide_shot.png, medium_shot.png\n"
        "  <b>Products</b> - Subfolder: <code>[Brand]_[ProductName]</code>\n"
        "    Required: product_front.png (transparent BG)\n"
        "  <b>B-Rolls</b> - Flat folder, .mp4 files, 3-8 seconds\n"
        "  <b>Voiceovers</b> - Clean dry speech for Kling lip-sync\n"
        "  <b>Audio/SFX</b> - Music and effects for post-production\n\n"
        "<b>Commands:</b>\n"
        "/index - Re-scan asset directories and update the database index\n"
        "/create_asset <code>[type] [description]</code>\n"
        "  Example: <code>/create_asset avatar A 25yo fitness female</code>\n"
        "/generate - Step-by-step video generation wizard\n"
        "  Selects avatar, location, product, task, and video length\n"
        "/cancel - Cancel any active wizard\n\n"
        "<b>Kling 3.0 Notes:</b>\n"
        "  - Max segment length: 15 seconds\n"
        "  - Videos longer than 15s use chaining (auto-segmented)\n"
        "  - Voiceovers must be clean/dry audio (no background music)\n"
        "  - Prompt structure: Scene > Character > Action > Camera > Style"
    )
