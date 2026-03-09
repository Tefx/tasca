"""Tests for local invar compatibility entrypoint."""

from __future__ import annotations

import importlib
import sys

from tasca.shell import invar_entrypoint


def test_install_missing_hooks_stub_injects_hooks_module(monkeypatch) -> None:
    """Missing hooks module is patched with a compatibility Typer app."""

    original_import_module = importlib.import_module

    def _raise_missing(name: str):
        if name == "invar.shell.commands.hooks":
            raise ModuleNotFoundError(name=name)
        return original_import_module(name)

    monkeypatch.setattr(invar_entrypoint.importlib, "import_module", _raise_missing)
    sys.modules.pop("invar.shell.commands.hooks", None)

    invar_entrypoint._install_missing_hooks_stub()

    hooks_module = sys.modules.get("invar.shell.commands.hooks")
    assert hooks_module is not None
    assert hasattr(hooks_module, "app")
