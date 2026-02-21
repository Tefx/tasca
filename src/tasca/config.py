"""
Application configuration using pydantic-settings.

Environment variables can be used to override defaults.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="TASCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    version: str = "0.1.0"
    debug: bool = False

    # Database
    db_path: str = "./data/tasca.db"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Security
    admin_token: str | None = None

    # CORS
    cors_origins: list[str] = []  # Empty = CORS disabled; ["*"] = allow all (no credentials)

    # Server-side limits (None = no limit)
    max_sayings_per_table: int | None = None  # Max sayings per table
    max_content_length: int | None = None  # Max characters per message
    max_bytes_per_table: int | None = None  # Max total bytes per table
    max_mentions_per_saying: int | None = None  # Max @mentions per saying


settings = Settings()
