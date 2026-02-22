"""
Authentication for admin operations.

This module provides token validation for admin-protected endpoints
and exports the OpenAPI security scheme for Bearer token authentication.
"""

import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tasca.config import settings

# @invar:allow shell_result: Pure predicate — returns bool, not Result; callers own error handling
# @invar:allow shell_pure_logic: Co-located with verify_admin_token for cohesion; no I/O but tightly coupled to auth.py
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


# OpenAPI security scheme for Bearer token authentication
# Registered in OpenAPI schema with scheme_name "bearerAuth"
# This makes the security requirement visible in /docs and /openapi.json
bearer_scheme = HTTPBearer(
    scheme_name="bearerAuth",
    description="Admin Bearer token authentication",
    auto_error=True,  # Returns 401 when no Bearer token is provided
)


# @shell_orchestration: FastAPI dependency that validates HTTP Authorization header and raises HTTPException
# @shell_complexity: Auth validation requires multiple branches (disabled, missing, malformed, invalid)
async def verify_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
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
    # Validate token using constant-time comparison (never log or print the token value)
    if not validate_bearer_token(credentials.credentials, settings.admin_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
        )

    # Valid token - return None to allow request to proceed
    return None
