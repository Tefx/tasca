"""
Health check routes.

Endpoints for monitoring application health.
"""

from tasca.shell.api.fastapi_compat import APIRouter

from tasca.config import settings

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """
    Health check endpoint.

    Returns basic health status and version.
    Does not expose any secrets.
    """
    return {
        "status": "healthy",
        "version": settings.version,
    }


@router.get("/ready")
async def readiness_check() -> dict[str, str]:
    """
    Readiness check endpoint.

    Verifies the application is ready to accept requests.
    """
    # TODO: Add database connectivity check when implemented
    return {"status": "ready"}
