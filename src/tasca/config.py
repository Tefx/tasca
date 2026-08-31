"""
Application configuration using pydantic-settings.

Environment variables can be used to override defaults.
"""

import os
import secrets
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from returns.result import Failure, Result, Success


def _get_version_result() -> Result[str, str]:
    """Get package version as a typed shell Result.

    Missing package metadata is a recoverable development/test condition.

    Examples:
        >>> isinstance(_get_version_result().unwrap(), str) or _get_version_result().failure()
        True
    """
    try:
        return Success(_pkg_version("tasca"))
    except PackageNotFoundError as exc:
        return Failure(str(exc))


_TOKEN_CLEAR_SENTINELS = {"null", "none", "clear"}


def _normalize_token(raw: str | None) -> Result[str | None, str]:
    """Normalize an optional environment token without retaining clear sentinels.

    Examples:
        >>> _normalize_token(None).unwrap() is None
        True
        >>> _normalize_token("  tk_secret  ").unwrap()
        'tk_secret'
        >>> _normalize_token("").unwrap() is None
        True
        >>> _normalize_token(" null ").unwrap() is None
        True
    """
    if raw is None:
        return Success(None)

    normalized = raw.strip()
    if not normalized or normalized.lower() in _TOKEN_CLEAR_SENTINELS:
        return Success(None)

    return Success(normalized)


def _normalize_admin_token(raw: str | None) -> Result[str | None, str]:
    """Normalize TASCA_ADMIN_TOKEN input from environment."""
    return _normalize_token(raw)


def _normalize_viewer_token(raw: str | None) -> Result[str | None, str]:
    """Normalize optional TASCA_VIEWER_TOKEN input from environment.

    Examples:
        >>> _normalize_viewer_token(" viewer-token ").unwrap()
        'viewer-token'
        >>> _normalize_viewer_token("clear").unwrap() is None
        True
    """
    return _normalize_token(raw)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="TASCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    version: str = Field(default_factory=lambda: _get_version_result().value_or("0.0.0"))
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
    admin_token: str = Field(
        default_factory=lambda: f"tk_{secrets.token_hex(16)}",
        repr=False,
    )
    # True when a non-clear source configured the token; used to redact startup output.
    admin_token_from_env: bool = False
    viewer_token: str | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def resolve_auth_tokens(self) -> "Settings":
        """Resolve environment credentials and reject ambiguous viewer access.

        TASCA_ADMIN_TOKEN retains its secure local fallback. TASCA_VIEWER_TOKEN
        is optional: absent, blank, and clear-sentinel values disable viewer
        authentication. A configured viewer credential must differ from the
        normalized admin credential.
        """
        admin_token_was_explicit = "admin_token" in self.model_fields_set
        raw_admin_token = os.getenv("TASCA_ADMIN_TOKEN")
        normalized_env_token = _normalize_admin_token(raw_admin_token).unwrap()
        configured_admin_token = _normalize_admin_token(self.admin_token).unwrap()

        if normalized_env_token is not None:
            self.admin_token = normalized_env_token
            self.admin_token_from_env = True
        elif admin_token_was_explicit and configured_admin_token is not None:
            self.admin_token = configured_admin_token
            self.admin_token_from_env = True
        else:
            self.admin_token_from_env = False
            self.admin_token = (
                configured_admin_token
                if configured_admin_token is not None
                else f"tk_{secrets.token_hex(16)}"
            )

        raw_viewer_token = os.getenv("TASCA_VIEWER_TOKEN")
        configured_viewer_token = (
            raw_viewer_token if raw_viewer_token is not None else self.viewer_token
        )
        self.viewer_token = _normalize_viewer_token(configured_viewer_token).unwrap()

        return self

    def __init__(self, **values: object) -> None:
        """Initialize settings and reject equal resolved credentials without echoing them."""
        super().__init__(**values)
        if self.viewer_token is not None and self.viewer_token == self.admin_token:
            raise ValueError("TASCA_VIEWER_TOKEN must differ from TASCA_ADMIN_TOKEN")

    @property
    def viewer_auth_required(self) -> bool:
        """Whether REST viewer authentication is configured."""
        return self.viewer_token is not None

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
