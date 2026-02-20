"""
FastAPI application factory.

This module creates and configures the FastAPI application instance.
The app serves both REST API routes and MCP server endpoints.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tasca.config import settings
from tasca.shell.api.routes import health, tables
from tasca.shell.mcp import mcp


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager for startup and shutdown events."""
    # Startup
    yield
    # Shutdown


# @invar:allow shell_result: FastAPI app factory - returns FastAPI, not Result
# @shell_orchestration: App configuration and wiring is orchestration, not business logic
def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Tasca API",
        description="A discussion table service for coding agents",
        version=settings.version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health.router, tags=["health"])
    app.include_router(tables.router, prefix="/tables", tags=["tables"])

    # Mount MCP server at /mcp
    # MCP tools are available at: POST /mcp (JSON-RPC)
    # MCP base URL for development: http://localhost:8000/mcp
    # Note: MCP HTTP transport uses Streamable HTTP stateful protocol.
    # For stdio transport, use: tasca-mcp command
    mcp_app = mcp.http_app()
    app.mount("/mcp", mcp_app)

    return app
