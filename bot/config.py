"""
Configuration module using pydantic-settings.
Loads settings from .env file with type validation and fail-fast behavior.
"""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Categories mapping: key -> relative directory path
CATEGORIES: dict[str, str] = {
    "avatars": "assets/avatars",
    "locations": "assets/locations",
    "products": "assets/products",
    "b_rolls": "assets/b_rolls",
    "voiceovers": "assets/voiceovers",
    "audio_sfx": "assets/audio_sfx",
}

# Categories that use structured subfolders (vs flat file storage)
SUBFOLDER_CATEGORIES: set[str] = {"avatars", "locations", "products"}

# Display labels for Telegram UI
CATEGORY_LABELS: dict[str, str] = {
    "avatars": "\U0001f9d1 Avatars",
    "locations": "\U0001f3e0 Locations",
    "products": "\U0001f4e6 Products",
    "b_rolls": "\U0001f39e B-Roll",
    "voiceovers": "\U0001f399 Voiceovers",
    "audio_sfx": "\U0001f3b5 Audio/SFX",
}


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    bot_token: SecretStr = Field(..., alias="BOT_TOKEN")
    admin_id: int = Field(..., alias="ADMIN_ID")
    base_dir: Path = Field(default=Path("."), alias="BASE_DIR")
    db_path: Path = Field(default=Path("data/assets.db"), alias="DB_PATH")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def asset_dirs(self) -> dict[str, Path]:
        """Full paths for all asset category directories."""
        return {category: self.base_dir / rel_path for category, rel_path in CATEGORIES.items()}

    @property
    def output_dir(self) -> Path:
        """Full path for video output directory."""
        return self.base_dir / "output" / "videos"

    @property
    def db_full_path(self) -> Path:
        """Full path for SQLite database."""
        return self.base_dir / self.db_path

    def ensure_directories(self) -> None:
        """Create all required directories if they don't exist."""
        for dir_path in self.asset_dirs.values():
            dir_path.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_full_path.parent.mkdir(parents=True, exist_ok=True)


# Singleton instance - imported by other modules
settings = Settings()
