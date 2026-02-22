"""
Entry point for the etone-media-engine Telegram bot.
Run with: uv run python -m bot
"""

import asyncio
import logging
import sys

from aiogram.types import BotCommand

from bot.config import settings
from bot.database import init_db
from bot.loader import create_bot, create_dispatcher

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Initialize all components and start polling."""
    logger.info("Starting etone-media-engine bot...")

    # 1. Ensure all asset directories exist
    settings.ensure_directories()
    logger.info("Asset directories verified.")

    # 2. Initialize SQLite database
    await init_db()
    logger.info("Database initialized at: %s", settings.db_full_path)

    # 3. Create bot and dispatcher
    bot = create_bot()
    dp = create_dispatcher()

    # 4. Set bot commands menu
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Start bot"),
            BotCommand(command="help", description="Show detailed help"),
            BotCommand(command="index", description="Reindex asset library"),
            BotCommand(command="create_asset", description="Create new asset folder"),
            BotCommand(command="generate", description="Generate video"),
            BotCommand(command="generator", description="Generator mode (HITL)"),
            BotCommand(command="cancel", description="Cancel current operation"),
        ]
    )
    logger.info("Bot commands registered.")

    # 5. Start polling
    logger.info("Admin ID: %d", settings.admin_id)
    logger.info("Bot is now polling for updates...")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
