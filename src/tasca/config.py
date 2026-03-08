"""
Application configuration using pydantic-settings.

Environment variables can be used to override defaults.
"""

import secrets
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# @invar:allow shell_result: Metadata lookup is expected to fail gracefully in dev/test environments
def _get_version() -> str:
    """Get package version, falling back to '0.0.0' if not installed."""
    try:
        return _pkg_version("tasca")
    except PackageNotFoundError:
        return "0.0.0"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="TASCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    version: str = Field(default_factory=_get_version)
    debug: bool = False
    environment: str = "development"  # "development" or "production"

    # Database (default: ~/.tasca/tasca.db; override via TASCA_DB_PATH)
    db_path: str = str(Path.home() / ".tasca" / "tasca.db")

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Security
    # Auto-generate a secure tk_-prefixed token if not set via env var
    # Format: tk_<32-hex-chars> (total 35 chars)
    admin_token: str = Field(default_factory=lambda: f"tk_{secrets.token_hex(16)}")
    admin_token_from_env: bool = False  # Set by model_validator if token came from env var

    @model_validator(mode="after")
    def set_admin_token_from_env(self) -> "Settings":
        """Check if admin_token was provided via TASCA_ADMIN_TOKEN environment variable."""
        # This runs after all fields are populated
        # We check if the env var exists (not if it matches, since pydantic already loaded it)
        import os

        self.admin_token_from_env = "TASCA_ADMIN_TOKEN" in os.environ
        return self

    # CORS
    cors_origins: list[str] = []  # Empty = CORS disabled; ["*"] = allow all (no credentials)

    # Content Security Policy
    # In production, CSP headers are enabled with restrictive settings
    # In development, CSP is more permissive or disabled for easier debugging
    csp_enabled: bool = True  # Set to False to disable CSP entirely
    csp_report_only: bool = False  # If True, reports violations without enforcing

    # Server-side limits (None = no limit)
    max_sayings_per_table: int | None = None  # Max sayings per table
    max_content_length: int | None = None  # Max characters per message
    max_bytes_per_table: int | None = None  # Max total bytes per table
    max_mentions_per_saying: int | None = None  # Max @mentions per saying

    @property
    def csp_header_value(self) -> str:
        """Build Content-Security-Policy header value based on environment.

        Production: Restrictive CSP for security
        Development: More permissive for debugging and hot reload
        """
        if not self.csp_enabled:
            return ""

        is_production = self.environment == "production"

        if is_production:
            # Production CSP: restrictive settings
            directives = [
                "default-src 'self'",
                "script-src 'self'",
                # SECURITY NOTE: 'unsafe-inline' in style-src is required for React inline styles.
                # Affected components: math.tsx (errorColor), MentionInput.tsx (positioning),
                # Table.tsx (layout). Alternative: nonce-based CSP with build-time transform.
                # Risk: Accepted residual XSS vector via CSS injection (lower severity than JS).
                # See: https://web.dev/strict-csp/#why-unsafe-inline-for-style-is-less-risky
                "style-src 'self' 'unsafe-inline'",
                "img-src 'self' data:",
                "connect-src 'self'",
                "font-src 'self'",
                "object-src 'none'",
                "base-uri 'none'",
                "frame-ancestors 'none'",
            ]
        else:
            # Development CSP: more permissive for debugging
            directives = [
                "default-src 'self' 'unsafe-inline' 'unsafe-eval'",
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
                "style-src 'self' 'unsafe-inline'",
                "img-src 'self' data: blob:",
                "connect-src 'self' ws: wss:",  # Allow WebSocket for hot reload
                "font-src 'self' data:",
            ]

        return "; ".join(directives)


settings = Settings()
