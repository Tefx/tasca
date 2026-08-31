"""
Health check routes.

Endpoints for monitoring application health.
"""

from tasca.config import settings
from tasca.shell.api.fastapi_compat import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str | bool]:
    """
    Health check endpoint.

    Returns basic health status, version, and viewer-auth state.
    Does not expose credentials.
    """
    return {
        "status": "healthy",
        "version": settings.version,
        "viewer_auth_required": settings.viewer_auth_required,
    }


@router.get("/ready")
async def readiness_check() -> dict[str, str]:
    """
    Readiness check endpoint.

    Verifies the application is ready to accept requests.
    """
    # TODO: Add database connectivity check when implemented
    return {"status": "ready"}
