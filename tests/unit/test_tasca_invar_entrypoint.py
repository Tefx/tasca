"""Tests for top-level invar console entrypoint shim."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

import tasca_invar_entrypoint


def test_main_reports_guidance_when_tasca_module_is_missing(monkeypatch) -> None:
    """Direct invar entrypoint emits guidance instead of traceback noise."""

    original_import = __import__

    def _fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> object:
        if name == "tasca.shell.invar_entrypoint":
            raise ModuleNotFoundError(name="tasca")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    with pytest.raises(SystemExit) as exc_info:
        tasca_invar_entrypoint.main()

    message = str(exc_info.value)
    assert "Unable to start direct `invar` entrypoint" in message
    assert "Missing module: tasca" in message
    assert "uv run invar guard --all" in message
