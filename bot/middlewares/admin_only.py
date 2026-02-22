"""
Global security middleware.
Intercepts every incoming update at the dispatcher level and silently drops
any updates from users whose Telegram ID does not match ADMIN_ID.
Registered as an outer middleware on dp.update to cover all event types.
"""

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


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
        # Silently drop updates from non-admin users
        return None
