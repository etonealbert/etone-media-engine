"""
CallbackData factories with short prefixes.
Each prefix is kept to 1-3 characters to maximize payload space
within Telegram's 64-byte callback_data limit.
"""

from aiogram.filters.callback_data import CallbackData


class CategoryCB(CallbackData, prefix="cat"):
    """Category selection for file uploads."""

    action: str  # "select"
    name: str  # "avatars", "locations", "products", etc.


class FolderCB(CallbackData, prefix="fld"):
    """Folder operations for subfolder-based categories."""

    action: str  # "new", "existing", "select"
    cat: str  # category short name
    id: int = 0  # DB asset id (for existing folder selection)


class AssetCB(CallbackData, prefix="ast"):
    """Asset selection and pagination."""

    action: str  # "select", "page"
    id: int = 0  # DB asset id
    page: int = 0  # pagination page number


class GenerateCB(CallbackData, prefix="gen"):
    """Video generation wizard callbacks."""

    action: str  # "avatar", "location", "product", "length", "confirm", "cancel"
    id: int = 0  # DB asset id for selected asset
    page: int = 0  # pagination page number


class LengthCB(CallbackData, prefix="len"):
    """Target video length selection."""

    seconds: int  # 15, 30, 45, 60


class ConfirmCB(CallbackData, prefix="cfm"):
    """Confirmation actions."""

    action: str  # "launch", "cancel"


class SkipCB(CallbackData, prefix="sk"):
    """Skip the current step in the wizard."""

    step: str  # "avatar", "location", "product", "task", "length", "context"


class CancelCB(CallbackData, prefix="x"):
    """Universal cancel callback."""

    pass
