"""
Unit tests for CSPMiddleware.

Tests verify Content-Security-Policy header behavior across environments
and configurations.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tasca.config import Settings
from tasca.shell.api.app import CSPMiddleware


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_settings(request: pytest.FixtureRequest) -> Settings:
    """Create test settings from parametrization."""
    return getattr(request, "param", Settings())


@pytest.fixture
def client(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client for the FastAPI app with patched settings.

    Uses monkeypatch to properly patch the settings where the middleware uses it.
    The middleware imports settings from tasca.config, so we patch at the use site.
    """
    # Patch the settings where CSPMiddleware imports it (in app.py)
    monkeypatch.setattr("tasca.shell.api.app.settings", test_settings)

    # Create minimal app with a simple route
    app = FastAPI()

    @app.get("/test")
    def test_route() -> dict:
        return {"status": "ok"}

    # Add CSP middleware
    app.add_middleware(CSPMiddleware)

    return TestClient(app)


# =============================================================================
# CSP Header Tests
# =============================================================================


class TestCSPMiddleware:
    """Tests for CSPMiddleware behavior across configurations."""

    @pytest.mark.parametrize(
        "test_settings",
        [
            Settings(
                environment="production",
                csp_enabled=True,
                csp_report_only=False,
            )
        ],
        indirect=True,
    )
    def test_production_csp_header_present(self, client: TestClient) -> None:
        """Production mode: CSP header present with restrictive directives."""
        response = client.get("/test")

        assert response.status_code == 200
        assert "Content-Security-Policy" in response.headers

        csp_value = response.headers["Content-Security-Policy"]

        # Verify production directives are present
        assert "default-src 'self'" in csp_value
        assert "script-src 'self'" in csp_value
        assert "style-src 'self' 'unsafe-inline'" in csp_value
        assert "img-src 'self' data:" in csp_value
        assert "connect-src 'self'" in csp_value
        assert "font-src 'self'" in csp_value
        assert "object-src 'none'" in csp_value
        assert "base-uri 'none'" in csp_value
        assert "frame-ancestors 'none'" in csp_value

        # Verify development-only directives are NOT present
        assert "'unsafe-eval'" not in csp_value
        assert "ws:" not in csp_value
        assert "wss:" not in csp_value
        assert "blob:" not in csp_value

    @pytest.mark.parametrize(
        "test_settings",
        [
            Settings(
                environment="development",
                csp_enabled=True,
                csp_report_only=False,
            )
        ],
        indirect=True,
    )
    def test_development_csp_header_present_permissive(self, client: TestClient) -> None:
        """Development mode: CSP header present but permissive for debugging."""
        response = client.get("/test")

        assert response.status_code == 200
        assert "Content-Security-Policy" in response.headers

        csp_value = response.headers["Content-Security-Policy"]

        # Verify development directives are present (more permissive)
        assert "default-src 'self' 'unsafe-inline' 'unsafe-eval'" in csp_value
        assert "script-src 'self' 'unsafe-inline' 'unsafe-eval'" in csp_value
        assert "style-src 'self' 'unsafe-inline'" in csp_value
        assert "img-src 'self' data: blob:" in csp_value
        assert "connect-src 'self' ws: wss:" in csp_value  # WebSocket for hot reload
        assert "font-src 'self' data:" in csp_value

        # Verify production-only directives are NOT present
        assert "object-src 'none'" not in csp_value
        assert "base-uri 'none'" not in csp_value
        assert "frame-ancestors 'none'" not in csp_value

    @pytest.mark.parametrize(
        "test_settings",
        [
            Settings(
                environment="production",
                csp_enabled=False,
                csp_report_only=False,
            )
        ],
        indirect=True,
    )
    def test_csp_disabled_header_absent(self, client: TestClient) -> None:
        """CSP disabled: No CSP header should be present."""
        response = client.get("/test")

        assert response.status_code == 200
        # Neither CSP header should be present
        assert "Content-Security-Policy" not in response.headers
        assert "Content-Security-Policy-Report-Only" not in response.headers

    @pytest.mark.parametrize(
        "test_settings",
        [
            Settings(
                environment="production",
                csp_enabled=True,
                csp_report_only=True,
            )
        ],
        indirect=True,
    )
    def test_report_only_mode_uses_report_only_header(self, client: TestClient) -> None:
        """Report-only mode: Uses Content-Security-Policy-Report-Only header."""
        response = client.get("/test")

        assert response.status_code == 200

        # Should use Report-Only header instead of enforcing CSP
        assert "Content-Security-Policy-Report-Only" in response.headers
        assert "Content-Security-Policy" not in response.headers

        csp_value = response.headers["Content-Security-Policy-Report-Only"]

        # Verify content is still production CSP directives
        assert "default-src 'self'" in csp_value
        assert "script-src 'self'" in csp_value
        assert "object-src 'none'" in csp_value

    @pytest.mark.parametrize(
        "test_settings",
        [
            Settings(
                environment="development",
                csp_enabled=True,
                csp_report_only=True,
            )
        ],
        indirect=True,
    )
    def test_report_only_mode_development(self, client: TestClient) -> None:
        """Report-only mode in development: Uses Report-Only header with permissive CSP."""
        response = client.get("/test")

        assert response.status_code == 200
        assert "Content-Security-Policy-Report-Only" in response.headers
        assert "Content-Security-Policy" not in response.headers

        csp_value = response.headers["Content-Security-Policy-Report-Only"]

        # Verify permissive development directives
        assert "'unsafe-eval'" in csp_value
        assert "ws: wss:" in csp_value


class TestCSPMiddlewareInteraction:
    """Tests for CSP middleware interaction with responses."""

    @pytest.mark.parametrize(
        "test_settings",
        [
            Settings(
                environment="production",
                csp_enabled=True,
                csp_report_only=False,
            )
        ],
        indirect=True,
    )
    def test_csp_header_added_to_all_responses(self, client: TestClient) -> None:
        """CSP header is added to all responses, not just HTML."""
        # Test JSON endpoint
        response = client.get("/test")
        assert "Content-Security-Policy" in response.headers

        # Test 404 response
        response = client.get("/nonexistent")
        assert "Content-Security-Policy" in response.headers

    @pytest.mark.parametrize(
        "test_settings",
        [
            Settings(
                environment="production",
                csp_enabled=True,
                csp_report_only=False,
            )
        ],
        indirect=True,
    )
    def test_csp_does_not_override_existing_headers(self, client: TestClient) -> None:
        """CSP middleware preserves other response headers."""
        response = client.get("/test")

        # Standard headers should still be present
        assert "content-type" in response.headers
        # CSP should be present
        assert "Content-Security-Policy" in response.headers
