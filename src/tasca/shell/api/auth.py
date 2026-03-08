"""
Authentication for admin operations.

This module provides token validation for admin-protected endpoints
and exports the OpenAPI security scheme for Bearer token authentication.

Escape Hatch Convention (shell_result):
    Auth helpers return bool or raise HTTPException, not Result[T, E].
    Use "HTTP auth" as the escape reason for these patterns.
"""

import hmac
from typing import TYPE_CHECKING

# FastAPI is a required runtime dependency for the API server. We use conditional
# imports to allow static analysis and doctest collection in environments where
# it's not installed (e.g., during guard runs or in minimal test environments).
if TYPE_CHECKING:
    from fastapi import Depends, HTTPException, status
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
else:
    try:
        from fastapi import Depends, HTTPException, status
        from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    except ImportError:
        # For static analysis/doctest collection in environments without fastapi,
        # we define type aliases that satisfy the type checker but will cause
        # runtime errors if the API server is actually run without fastapi.
        Depends = None  # type: ignore[misc,assignment]
        HTTPException = Exception  # type: ignore[misc,assignment]
        status = type("status", (), {})()  # type: ignore[misc,assignment]
        HTTPAuthorizationCredentials = None  # type: ignore[misc,assignment]
        HTTPBearer = None  # type: ignore[misc,assignment]

from tasca.config import settings


# @invar:allow shell_result: HTTP auth
# @shell_orchestration: Co-located with FastAPI dependency to keep auth flow and failure semantics together
def validate_bearer_token(token: str, expected: str) -> bool:
    """Compare a Bearer token against the expected value using constant-time comparison.

    Uses ``hmac.compare_digest`` to prevent timing attacks. Both arguments must
    be non-empty strings; callers are responsible for checking whether auth is
    required before invoking this helper.

    Args:
        token: The token extracted from the Authorization header.
        expected: The expected (configured) token value.

    Returns:
        True if the token matches the expected value, False otherwise.

    Examples:
        >>> validate_bearer_token("secret", "secret")
        True
        >>> validate_bearer_token("wrong", "secret")
        False
        >>> validate_bearer_token("", "secret")
        False
    """
    return hmac.compare_digest(token, expected)


# @invar:allow shell_result: FastAPI security scheme instantiation - not a Result type
def _get_bearer_scheme() -> "HTTPBearer":
    """Lazy initialization of HTTPBearer to avoid import-time errors without fastapi."""
    return HTTPBearer(
        scheme_name="bearerAuth",
        description="Admin Bearer token authentication",
        auto_error=True,  # Returns 401 when no Bearer token is provided
    )


# @invar:allow shell_result: FastAPI security scheme instantiation - not a Result type
# Exposed as a callable for FastAPI Depends() - initializes on first use
def bearer_scheme() -> "HTTPBearer":
    """Get the HTTPBearer security scheme, initializing if needed."""
    global _bearer_scheme
    if _bearer_scheme is None:
        _bearer_scheme = _get_bearer_scheme()
    return _bearer_scheme


# Module-level sentinel - initialized lazily
_bearer_scheme: "HTTPBearer | None" = None


# @shell_orchestration: FastAPI dependency that validates HTTP Authorization header and raises HTTPException
# @shell_complexity: Auth validation requires multiple branches (disabled, missing, malformed, invalid)
async def verify_admin_token(
    credentials: "HTTPAuthorizationCredentials | None" = None,
) -> None:
    """
    Verify admin Bearer token for protected endpoints.

    HTTPBearer extracts and validates the Bearer token format,
    then this function validates the token value.

    Args:
        credentials: HTTPAuthorizationCredentials from HTTPBearer
            (contains .scheme and .credentials attributes).

    Returns:
        None on success (valid token).

    Raises:
        HTTPException: 401 if token is missing or invalid.

    Security:
        Token value is never logged or exposed in error messages.

    Examples:
        >>> # Valid token
        >>> # Authorization: "Bearer correct-token"
        >>> # Returns None

        >>> # Invalid token
        >>> # Authorization: "Bearer wrong-token"
        >>> # Raises HTTPException(401, "Invalid or missing token")
    """
    # Handle lazy initialization of bearer_scheme for environments without fastapi
    # This avoids import-time errors during doctest collection

    # Validate token using constant-time comparison (never log or print the token value)
    token = credentials.credentials if credentials else None
    if not validate_bearer_token(token or "", settings.admin_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
        )

    # Valid token - return None to allow request to proceed
    return None
