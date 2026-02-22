"""
Bot and Dispatcher factory module.
Creates and configures the Bot instance, Dispatcher, and assembles all routers.
"""

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import settings
from bot.handlers import asset_manager, common, generate
from bot.middlewares.admin_only import AdminOnlyMiddleware

logger = logging.getLogger(__name__)


def create_bot() -> Bot:
    """
    Create and configure the Bot instance.
    Uses DefaultBotProperties for global HTML parse mode.
    """
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )


def create_dispatcher() -> Dispatcher:
    """
    Create and configure the Dispatcher.

    - Uses MemoryStorage for FSM (suitable for single-admin bot).
      For production with frequent restarts, swap to RedisStorage:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url("redis://localhost:6379/0")

    - Registers AdminOnlyMiddleware globally on dp.update (outer scope)
      to block all non-admin users before any handler processes them.

    - Includes routers in priority order:
      1. common_router  - /cancel has priority over state-specific handlers
      2. asset_manager_router - /index, /create_asset, file uploads
      3. generate_router - /generate FSM wizard
    """
    # FSM Storage: MemoryStorage for development.
    # For production resilience (survives bot restarts):
    #   storage = RedisStorage.from_url("redis://localhost:6379/0")
    storage = MemoryStorage()

    dp = Dispatcher(storage=storage)

    # Global security: drop all updates from non-admin users
    dp.update.outer_middleware(AdminOnlyMiddleware(settings.admin_id))

    # Include routers in priority order
    # common_router MUST be first so /cancel intercepts before state handlers
    dp.include_router(common.router)
    dp.include_router(asset_manager.router)
    dp.include_router(generate.router)

    logger.info(
        "Dispatcher configured with %d routers, admin_id=%d",
        len(dp.sub_routers),
        settings.admin_id,
    )

    return dp
