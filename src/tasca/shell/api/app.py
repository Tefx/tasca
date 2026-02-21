"""
FastAPI application factory.

This module creates and configures the FastAPI application instance.
The app serves both REST API routes and MCP server endpoints.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tasca.config import settings
from tasca.shell.api.routes import export, health, sayings, seats, tables
from tasca.shell.api.routes import search
from tasca.shell.mcp import mcp

logger = logging.getLogger(__name__)


# @invar:allow shell_result: FastAPI app factory - returns FastAPI, not Result
# @shell_orchestration: App configuration and wiring is orchestration, not business logic
def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    The MCP server is mounted at /mcp using the HTTP transport (Streamable HTTP).
    MCP tools are available via JSON-RPC at POST /mcp/mcp.

    Development MCP base URL: http://localhost:8000/mcp/mcp
    For stdio transport, use the tasca-mcp command directly.
    """
    # Get MCP HTTP app first - we need its lifespan
    mcp_app = mcp.http_app()

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

    # Include routers under /api/v1 prefix
    API_V1_PREFIX = "/api/v1"
    app.include_router(health.router, prefix=API_V1_PREFIX, tags=["health"])
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
    # MCP HTTP transport endpoint: POST /mcp/mcp (JSON-RPC)
    # The mcp_app has an internal route at /mcp, so the full path is /mcp/mcp
    app.mount("/mcp", mcp_app)
    logger.info("MCP server mounted at /mcp (endpoint: POST /mcp/mcp)")

    return app
