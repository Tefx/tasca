"""
Pytest configuration and fixtures for Tasca tests.
"""

import pytest


@pytest.fixture
def anyio_backend():
    """Configure anyio for async tests."""
    return "asyncio"
