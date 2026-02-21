"""
Authentication for admin operations.

This module provides token validation for admin-protected endpoints
and exports the OpenAPI security scheme for Bearer token authentication.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tasca.config import settings

# OpenAPI security scheme for Bearer token authentication
# Registered in OpenAPI schema with scheme_name "bearerAuth"
# This makes the security requirement visible in /docs and /openapi.json
bearer_scheme = HTTPBearer(
    scheme_name="bearerAuth",
    description="Admin Bearer token authentication",
    auto_error=True,  # Raise 403 if credentials missing (we convert to 401)
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
        None on success (either auth disabled or valid token).

    Raises:
        HTTPException: 401 if token is missing or invalid.

    Security:
        Token value is never logged or exposed in error messages.

    Examples:
        >>> # Auth disabled (settings.admin_token is None)
        >>> # Returns None without checking

        >>> # Valid token
        >>> # Authorization: "Bearer correct-token"
        >>> # Returns None

        >>> # Invalid token
        >>> # Authorization: "Bearer wrong-token"
        >>> # Raises HTTPException(401, "Invalid or missing token")
    """
    # If admin_token is not configured, authentication is disabled
    if settings.admin_token is None:
        return None

    # HTTPBearer with auto_error=True raises 403 for missing credentials,
    # but we want 401 for consistency. Handle the edge case here.
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
        )

    # Validate token (never log or print the token value)
    if credentials.credentials != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
        )

    # Valid token - return None to allow request to proceed
    return None
