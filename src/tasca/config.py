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


settings = Settings()
