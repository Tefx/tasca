"""Assertions for standardized REST error envelopes in route-unit tests."""

from __future__ import annotations

from typing import Any

from httpx import Response


def assert_detail_error(
    response: Response,
    *,
    code: str,
    message_contains: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Assert the FastAPI HTTPException ``detail`` contains the standard envelope."""
    detail = response.json()["detail"]
    assert detail["error"]["code"] == code
    if message_contains is not None:
        assert message_contains in detail["error"]["message"]
    if details is not None:
        assert detail["error"]["details"] == details
