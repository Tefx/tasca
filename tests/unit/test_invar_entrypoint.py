"""Tests for local invar compatibility entrypoint."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

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


def test_enforce_changed_files_policy_rejects_zero_file_guard(monkeypatch) -> None:
    """Guard default mode exits early when no changed Python files exist."""

    monkeypatch.setattr(invar_entrypoint, "_list_changed_python_files", lambda _: set())

    with pytest.raises(SystemExit, match="no changed Python files found"):
        invar_entrypoint._enforce_changed_files_policy(["guard"], Path("."))


def test_enforce_changed_files_policy_allows_guard_all(monkeypatch) -> None:
    """Explicit --all bypasses changed-files gate."""

    monkeypatch.setattr(invar_entrypoint, "_list_changed_python_files", lambda _: set())

    invar_entrypoint._enforce_changed_files_policy(["guard", "--all"], Path("."))


def test_enforce_changed_files_policy_allows_explicit_target(monkeypatch) -> None:
    """Positional target bypasses changed-files gate."""

    monkeypatch.setattr(invar_entrypoint, "_list_changed_python_files", lambda _: set())

    invar_entrypoint._enforce_changed_files_policy(
        ["guard", "src/tasca/shell/invar_entrypoint.py"], Path(".")
    )


def test_main_installs_hooks_stub_before_importing_guard(monkeypatch) -> None:
    """main() should dispatch guard app without crashing on missing hooks."""

    called = {"stub": False, "app": False}

    def _stub() -> None:
        called["stub"] = True

    guard_module = types.ModuleType("invar.shell.commands.guard")

    def _app() -> None:
        called["app"] = True

    guard_module.__dict__["app"] = _app

    monkeypatch.setattr(invar_entrypoint, "_install_missing_hooks_stub", _stub)
    monkeypatch.setattr(invar_entrypoint, "_enforce_changed_files_policy", lambda *_: None)

    original_guard_module = sys.modules.get("invar.shell.commands.guard")
    sys.modules["invar.shell.commands.guard"] = guard_module
    try:
        invar_entrypoint.main()
    finally:
        if original_guard_module is None:
            sys.modules.pop("invar.shell.commands.guard", None)
        else:
            sys.modules["invar.shell.commands.guard"] = original_guard_module

    assert called == {"stub": True, "app": True}
