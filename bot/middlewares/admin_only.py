"""
Global security middleware.
Intercepts every incoming update at the dispatcher level and drops
any updates from users whose Telegram ID does not match ADMIN_ID.
For message and callback events, replies with a short denial notice.
Registered as an outer middleware on dp.update to cover all event types.
"""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)

_DENY_TEXT = "⛔ You do not have access to this bot."


class AdminOnlyMiddleware(BaseMiddleware):
    """Drop all updates from non-admin users before they reach any handler."""

    def __init__(self, admin_id: int) -> None:
        self.admin_id = admin_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # event_from_user is injected by aiogram for all update types
        user = data.get("event_from_user")
        if user and user.id == self.admin_id:
            return await handler(event, data)

        # Notify unauthorized users on message / callback events (fail-safe)
        try:
            update = data.get("event_update", event)
            if isinstance(getattr(update, "message", None), Message):
                await update.message.answer(_DENY_TEXT)
            elif isinstance(getattr(update, "callback_query", None), CallbackQuery):
                await update.callback_query.answer(_DENY_TEXT, show_alert=True)
        except Exception:
            logger.debug("Could not send denial notice to user %s", user)

        return None
