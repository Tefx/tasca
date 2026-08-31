"""Unit tests for viewer-token configuration and auth helpers."""

from __future__ import annotations

import pytest

from tasca.config import Settings, _normalize_viewer_token


@pytest.mark.parametrize("raw", [None, "", "   ", "null", " NONE ", "clear"])
def test_viewer_token_clear_values_disable_auth(raw: str | None) -> None:
    """Absent, blank, and documented clear sentinels disable viewer auth."""
    assert _normalize_viewer_token(raw).unwrap() is None


def test_settings_normalizes_configured_viewer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured viewer credentials are stripped and enable REST viewer auth."""
    monkeypatch.delenv("TASCA_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("TASCA_VIEWER_TOKEN", raising=False)

    configured = Settings(admin_token="admin-unit-token", viewer_token=" viewer-unit-token ")

    assert configured.admin_token == "admin-unit-token"
    assert configured.admin_token_from_env is True
    assert configured.viewer_token == "viewer-unit-token"
    assert configured.viewer_auth_required is True


def test_settings_preserves_generated_admin_fallback_when_viewer_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clearing an admin token still produces the local generated credential."""
    monkeypatch.delenv("TASCA_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("TASCA_VIEWER_TOKEN", " clear ")

    configured = Settings(admin_token="clear")

    assert configured.admin_token.startswith("tk_")
    assert configured.admin_token_from_env is False
    assert configured.viewer_token is None
    assert configured.viewer_auth_required is False


def test_environment_admin_token_is_marked_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process environment token is configured and therefore redacted."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "configured-environment-token")

    configured = Settings()

    assert configured.admin_token_from_env is True


def test_dotenv_admin_token_is_marked_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Pydantic .env sources are treated as configured and must be redacted."""
    monkeypatch.delenv("TASCA_ADMIN_TOKEN", raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("TASCA_ADMIN_TOKEN=configured-dotenv-token\n", encoding="utf-8")

    configured = Settings(_env_file=dotenv_path)

    assert configured.admin_token == "configured-dotenv-token"
    assert configured.admin_token_from_env is True


def test_settings_rejects_equal_normalized_credentials_without_secret_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equality rejection names configuration keys but never the credential value."""
    secret = "same-normalized-token"
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", f" {secret} ")
    monkeypatch.setenv("TASCA_VIEWER_TOKEN", secret)

    with pytest.raises(ValueError) as raised:
        Settings()

    message = str(raised.value)
    assert "TASCA_VIEWER_TOKEN must differ from TASCA_ADMIN_TOKEN" in message
    assert secret not in message
