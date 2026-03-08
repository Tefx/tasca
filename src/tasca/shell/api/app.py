"""
FastAPI application factory.

This module creates and configures the FastAPI application instance.
The app serves both REST API routes and MCP server endpoints.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

# FastAPI is a required runtime dependency for the API server. We use conditional
# imports to allow static analysis and doctest collection in environments where
# it's not installed (e.g., during guard runs or in minimal test environments).
if TYPE_CHECKING:
    from fastapi import FastAPI, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
else:
    try:
        from fastapi import FastAPI, Request, Response
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.staticfiles import StaticFiles
    except ImportError:
        # For static analysis/doctest collection in environments without fastapi
        FastAPI = None  # type: ignore[misc,assignment]
        Request = None  # type: ignore[misc,assignment]
        Response = None  # type: ignore[misc,assignment]
        CORSMiddleware = None  # type: ignore[misc,assignment]
        StaticFiles = None  # type: ignore[misc,assignment]

# Starlette is also needed by FastAPI - handle conditionally
try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import FileResponse, JSONResponse
    from starlette.types import ASGIApp, Message, Receive, Scope, Send
except ImportError:
    BaseHTTPMiddleware = object  # type: ignore[misc,assignment]
    FileResponse = None  # type: ignore[misc,assignment]
    JSONResponse = None  # type: ignore[misc,assignment]
    ASGIApp = None  # type: ignore[misc,assignment]
    Message = None  # type: ignore[misc,assignment]
    Receive = None  # type: ignore[misc,assignment]
    Scope = None  # type: ignore[misc,assignment]
    Send = None  # type: ignore[misc,assignment]

from tasca.config import settings
from tasca.shell.api.auth import validate_bearer_token
from tasca.shell.mcp import mcp

logger = logging.getLogger(__name__)


class MCPBearerAuthMiddleware:
    """Middleware to validate Bearer token for MCP HTTP endpoint.

    Validates Authorization: Bearer <token> against settings.admin_token.
    If admin_token is None or empty, authentication is bypassed (allow through).

    This middleware wraps the MCP HTTP app and only applies to /mcp requests.
    STDIO transport is unaffected.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the middleware with the ASGI app to wrap.

        Args:
            app: The ASGI application to wrap.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process the ASGI request.

        For HTTP requests to MCP endpoint:
        - Extract Authorization header
        - Validate Bearer token against settings.admin_token
        - Return 401 if token is missing or invalid (when admin_token is set)
        - Allow through if admin_token is None/empty

        Args:
            scope: ASGI scope dictionary.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        # Only authenticate HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Pass OPTIONS preflight through without auth (CORS).
        # Although the scope["type"] != "http" check above already excludes non-HTTP
        # events (WebSocket, lifespan), browser CORS preflight sends OPTIONS over HTTP
        # and must reach the app without a Bearer token — browsers never attach
        # credentials to preflight requests.
        if scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # Get authorization header (safe first-match; dict() would drop duplicates)
        token_header = next(
            (v for k, v in scope.get("headers", []) if k == b"authorization"),
            b"",
        )
        auth_header = token_header.decode("utf-8")

        # If admin_token is None or empty, allow through
        if not settings.admin_token:
            await self.app(scope, receive, send)
            return

        # Validate Bearer token
        if not auth_header.startswith("Bearer "):
            await self._send_401(scope, send, "Missing or invalid Authorization header")
            return

        token = auth_header[7:]  # Remove "Bearer " prefix

        # Use constant-time comparison to prevent timing attacks
        if not validate_bearer_token(token, settings.admin_token):
            await self._send_401(scope, send, "Invalid or missing token")
            return

        # Token is valid, proceed to app
        await self.app(scope, receive, send)

    async def _send_401(self, scope: Scope, send: Send, detail: str) -> None:
        """Send a 401 Unauthorized JSON response.

        Args:
            scope: ASGI scope dictionary.
            send: ASGI send callable.
            detail: Error detail message.
        """
        from starlette.responses import JSONResponse

        response = JSONResponse(
            status_code=401,
            content={"detail": detail},
        )

        # Create a minimal receive callable for the response
        async def receive_empty() -> Message:
            return {"type": "http.request", "body": b""}

        await response(scope, receive_empty, send)


class CSPMiddleware(BaseHTTPMiddleware):
    """Middleware to add Content-Security-Policy headers to responses.

    In production, this provides XSS protection by restricting resource sources.
    In development, CSP is more permissive to allow debugging tools.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        # Only add CSP if enabled and header value is non-empty
        if settings.csp_enabled and settings.csp_header_value:
            header_name = (
                "Content-Security-Policy-Report-Only"
                if settings.csp_report_only
                else "Content-Security-Policy"
            )
            response.headers[header_name] = settings.csp_header_value

        return response


# @shell_complexity: 4 branches for middleware wiring + CORS + router mount + exception handlers
# @invar:allow shell_result: FastAPI app factory - returns FastAPI, not Result
# @shell_orchestration: App configuration and wiring is orchestration, not business logic
def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    The MCP server is mounted at /mcp using the HTTP transport (Streamable HTTP).
    MCP tools are available via JSON-RPC at POST /mcp.

    Development MCP base URL: http://localhost:8000/mcp
    For stdio transport, use the tasca-mcp command directly.

    Authentication:
    - MCP HTTP endpoint requires Bearer token if admin_token is configured.
    - If admin_token is None/empty, authentication is bypassed.
    - STDIO transport (tasca-mcp command) is unaffected by HTTP auth.
    """
    if FastAPI is None:
        raise ModuleNotFoundError(
            "fastapi is required to create the API application. "
            "Install tasca with API dependencies."
        )

    from tasca.shell.api.routes import export, health, patrons, sayings, search, seats, tables

    # Get MCP HTTP app first - we need its lifespan
    mcp_app = mcp.http_app(path="/")

    # Wrap MCP app with Bearer auth middleware
    # This applies authentication to all /mcp requests
    mcp_app_with_auth = MCPBearerAuthMiddleware(mcp_app)

    # Create FastAPI app with MCP's lifespan (required for Streamable HTTP protocol)
    app = FastAPI(
        title="Tasca API",
        description="A discussion table service for coding agents",
        version=settings.version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=mcp_app.lifespan,
    )

    # CORS middleware - only enabled if origins are configured
    # Note: allow_credentials=True requires specific origins, not "*"
    # See: https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS/Errors/CORSNotSupportingCredentials
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # CSP middleware - adds Content-Security-Policy headers for XSS protection
    # In production: restrictive CSP; In development: permissive for debugging
    app.add_middleware(CSPMiddleware)

    # Include routers under /api/v1 prefix
    API_V1_PREFIX = "/api/v1"
    app.include_router(health.router, prefix=API_V1_PREFIX, tags=["health"])
    app.include_router(patrons.router, prefix=f"{API_V1_PREFIX}/patrons", tags=["patrons"])
    app.include_router(tables.router, prefix=f"{API_V1_PREFIX}/tables", tags=["tables"])
    app.include_router(
        sayings.router, prefix=f"{API_V1_PREFIX}/tables/{{table_id}}/sayings", tags=["sayings"]
    )
    app.include_router(
        seats.router, prefix=f"{API_V1_PREFIX}/tables/{{table_id}}/seats", tags=["seats"]
    )
    app.include_router(search.router, prefix=f"{API_V1_PREFIX}/search", tags=["search"])
    app.include_router(
        export.router, prefix=f"{API_V1_PREFIX}/tables/{{table_id}}/export", tags=["export"]
    )

    # Mount MCP server at /mcp
    # MCP HTTP transport endpoint: POST /mcp/ (JSON-RPC) - note trailing slash
    # The mcp_app uses path="/", so the full endpoint is /mcp/
    # Auth middleware is applied via MCPBearerAuthMiddleware wrapper
    app.mount("/mcp", mcp_app_with_auth)

    # Redirect POST /mcp -> POST /mcp/ for client compatibility
    # (Starlette mounts don't auto-redirect POST requests without trailing slash)
    @app.post("/mcp", include_in_schema=False)
    async def redirect_mcp_post(request: Request) -> Response:
        """Redirect POST /mcp to POST /mcp/ for MCP client compatibility."""
        return Response(
            status_code=307,
            headers={"Location": "/mcp/"},
        )

    logger.info("MCP server mounted at /mcp (endpoint: POST /mcp/)")

    # Serve the React SPA from web/dist (if built).
    # /assets → static files; everything else → index.html (React Router).
    # Note: SPA fallback excludes /api/* and /mcp/* paths to allow proper 404 handling.
    _web_dist = Path(__file__).parents[2] / "web" / "dist"
    if _web_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=_web_dist / "assets"), name="web-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> Response:  # @invar:allow shell_result: SPA fallback
            # Exclude API paths from SPA fallback - they should 404 if not found
            # Note: MCP paths are handled by the mounted MCP app, not SPA fallback
            if full_path.startswith("api/"):
                return JSONResponse(
                    status_code=404,
                    content={"detail": "Not Found"},
                )
            candidate = _web_dist / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(_web_dist / "index.html")

    return app
