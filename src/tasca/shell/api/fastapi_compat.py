"""FastAPI compatibility layer for environments without FastAPI installed.

This module keeps import-time behavior stable for guard/doctest collection.
When FastAPI is present, it re-exports real FastAPI symbols.
When FastAPI is missing, it exposes lightweight no-op fallbacks so modules can
still be imported during static analysis.
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, status
    from fastapi.responses import Response
except ImportError:

    class HTTPException(Exception):
        """Fallback HTTPException for import-time compatibility."""

        def __init__(self, status_code: int, detail: str) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _StatusCodes:
        HTTP_200_OK = 200
        HTTP_201_CREATED = 201
        HTTP_400_BAD_REQUEST = 400
        HTTP_401_UNAUTHORIZED = 401
        HTTP_403_FORBIDDEN = 403
        HTTP_404_NOT_FOUND = 404
        HTTP_409_CONFLICT = 409
        HTTP_413_REQUEST_ENTITY_TOO_LARGE = 413
        HTTP_500_INTERNAL_SERVER_ERROR = 500

    class Response:  # type: ignore[no-redef]
        """Fallback response object for import-time compatibility."""

        def __init__(
            self,
            content: Any = None,
            media_type: str | None = None,
            headers: dict[str, str] | None = None,
            status_code: int = 200,
        ) -> None:
            self.content = content
            self.media_type = media_type
            self.headers = headers
            self.status_code = status_code

    class APIRouter:  # type: ignore[no-redef]
        """Fallback router that makes decorators no-op when FastAPI is absent."""

        def include_router(self, _router: Any) -> None:
            return None

        def get(self, *_args: Any, **_kwargs: Any):
            def decorator(func: Any) -> Any:
                return func

            return decorator

        post = get
        put = get
        patch = get
        delete = get

    def Depends(_dependency: Any) -> None:
        return None

    def Query(default: Any = None, **_kwargs: Any) -> Any:
        return default

    status = _StatusCodes()
