"""
Authentication dependencies for REST and MCP HTTP operations.

REST resource routes accept a configured viewer or admin credential. Existing
admin-only mutations retain their admin-only dependency. MCP HTTP always
accepts only the admin credential.

Escape Hatch Convention (shell_result):
    Auth helpers return bool or raise HTTPException, not Result[T, E].
    Use "HTTP auth" as the escape reason for these patterns.
"""

import hmac
from typing import TYPE_CHECKING, Literal, NoReturn

from returns.result import Failure, Result, Success

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


# @shell_orchestration: Co-located with FastAPI dependency to keep auth flow and failure semantics together
def validate_bearer_token(token: str | None, expected: str | None) -> Result[bool, str]:
    """Compare a Bearer token against the expected value using constant-time comparison.

    Uses ``hmac.compare_digest`` to prevent timing attacks.
    Missing/empty values always fail closed.

    Args:
        token: The token extracted from the Authorization header.
        expected: The expected (configured) token value.

    Returns:
        True if the token matches the expected value, False otherwise.

    Examples:
        >>> validate_bearer_token("secret", "secret")
        <Success: True>
        >>> validate_bearer_token("wrong", "secret")
        <Success: False>
        >>> validate_bearer_token("", "secret")
        <Success: False>
        >>> validate_bearer_token("secret", "")
        <Success: False>
        >>> validate_bearer_token(None, "secret")
        <Success: False>
    """
    if token is None or expected is None:
        return Success(False)

    normalized_token = token.strip()
    normalized_expected = expected.strip()
    if not normalized_token or not normalized_expected:
        return Success(False)

    return Success(hmac.compare_digest(normalized_token, normalized_expected))


def _get_bearer_scheme() -> Result["HTTPBearer", str]:
    """Lazy initialization of HTTPBearer to avoid import-time errors without fastapi."""
    return Success(HTTPBearer(
        scheme_name="bearerAuth",
        description="Viewer or admin Bearer token authentication",
        auto_error=False,  # verify_admin_token shapes all auth failures consistently
    ))


# Exposed as a callable for FastAPI Depends() - initializes on first use
def bearer_scheme() -> Result["HTTPBearer", str]:
    """Get the HTTPBearer security scheme, initializing if needed."""
    global _bearer_scheme
    if _bearer_scheme is None:
        scheme_result = _get_bearer_scheme()
        if isinstance(scheme_result, Failure):
            return Failure(scheme_result.failure())
        _bearer_scheme = scheme_result.unwrap()
    return Success(_bearer_scheme)


# Module-level sentinel - initialized lazily
_bearer_scheme: "HTTPBearer | None" = None

# FastAPI dependency default for credentials extraction.
# Source: Route dependencies use Depends(verify_admin_token), so this function
# must declare how credentials are injected from Authorization header.
if Depends is not None:
    _scheme_result = bearer_scheme()
    if isinstance(_scheme_result, Failure):
        raise RuntimeError(_scheme_result.failure())
    _credentials_dependency = Depends(_scheme_result.unwrap())
else:
    _credentials_dependency = None


AuthRole = Literal["viewer", "admin"]


# @invar:allow shell_result: Deterministic HTTP auth comparison returns a boolean for FastAPI dependencies.
def _token_matches(token: str | None, expected: str | None) -> bool:
    """Return whether a credential matches its expected token."""
    validation = validate_bearer_token(token, expected)
    return not isinstance(validation, Failure) and validation.unwrap()


# @invar:allow shell_result: HTTP auth role selection is deterministic dependency wiring.
def _credential_role(token: str | None, *, allow_viewer: bool) -> AuthRole | None:
    """Resolve the role for a valid token without exposing credential values."""
    if _token_matches(token, settings.admin_token):
        return "admin"
    if allow_viewer and _token_matches(token, settings.viewer_token):
        return "viewer"
    return None


# @invar:allow shell_result: HTTP auth failure must raise FastAPI's standard exception.
def _raise_permission_denied() -> NoReturn:
    """Raise the standard REST authorization failure without token details."""
    from tasca.shell.api.errors import error_envelope

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=error_envelope("PermissionDenied", "Invalid or missing token"),
    )


# @shell_orchestration: FastAPI dependency validates an admin-only mutation credential.
async def verify_admin_token(
    credentials: "HTTPAuthorizationCredentials | None" = _credentials_dependency,
) -> None:
    """Require the configured admin credential for an admin-only endpoint."""
    token = credentials.credentials if credentials else None
    if not _token_matches(token, settings.admin_token):
        _raise_permission_denied()


# @shell_orchestration: FastAPI dependency adds optional viewer authentication at router boundaries.
# @invar:allow shell_result: FastAPI dependency returns a role or raises HTTPException.
async def verify_viewer_or_admin(
    credentials: "HTTPAuthorizationCredentials | None" = _credentials_dependency,
) -> AuthRole | None:
    """Allow REST resource access to a viewer or admin when viewer auth is enabled.

    Disabled viewer auth deliberately returns without examining credentials so
    existing public REST reads and non-admin operations keep their baseline
    behavior.
    """
    if not settings.viewer_auth_required:
        return None

    token = credentials.credentials if credentials else None
    role = _credential_role(token, allow_viewer=True)
    if role is None:
        _raise_permission_denied()
    return role


# @shell_orchestration: FastAPI dependency validates the side-effect-free auth probe.
# @invar:allow shell_result: FastAPI dependency returns a role or raises HTTPException.
async def validate_authentication_token(
    credentials: "HTTPAuthorizationCredentials | None" = _credentials_dependency,
) -> AuthRole:
    """Return the authenticated role for GET /api/v1/auth/validate.

    Without configured viewer auth, absent credentials identify the public
    baseline viewer role. A supplied credential must still be valid: admin
    remains valid in either mode and viewer credentials work only when enabled.
    """
    if credentials is None and not settings.viewer_auth_required:
        return "viewer"

    token = credentials.credentials if credentials else None
    role = _credential_role(token, allow_viewer=settings.viewer_auth_required)
    if role is None:
        _raise_permission_denied()
    return role
