"""Tests for local invar compatibility entrypoint."""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Sequence
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


def test_install_missing_hooks_stub_allows_missing_invar_root(monkeypatch) -> None:
    """Missing root invar package is tolerated for uvx fallback path."""

    def _raise_missing(name: str):
        raise ModuleNotFoundError(name="invar")

    monkeypatch.setattr(invar_entrypoint.importlib, "import_module", _raise_missing)

    invar_entrypoint._install_missing_hooks_stub()


def test_uv_run_guard_zero_file_policy_rejects_zero_file_set(monkeypatch) -> None:
    """`uv run invar guard` default-mode policy still rejects zero-file scope."""

    monkeypatch.setattr(invar_entrypoint, "_list_changed_python_files", lambda _: set())

    with pytest.raises(SystemExit, match="no changed Python files found"):
        invar_entrypoint._enforce_changed_files_policy(["guard"], Path("."))


def test_run_guard_app_reports_guidance_for_non_fallback_module_missing(monkeypatch) -> None:
    """Raw direct guard startup reports guidance instead of traceback noise."""

    original_import = __import__

    def _fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> object:
        if name == "invar.shell.commands.guard":
            raise ModuleNotFoundError(name="invar.shell.commands.hooks")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    with pytest.raises(SystemExit) as exc_info:
        invar_entrypoint._run_guard_app(["guard"])

    message = str(exc_info.value)
    assert "Unable to start `invar guard`: missing module dependency." in message
    assert "Missing module: invar.shell.commands.hooks" in message
    assert "uv run invar guard --all" in message


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


def test_enforce_supported_invocation_rejects_non_repo_binary() -> None:
    """Direct invar binary outside repo venv exits with guidance."""

    repo_root = Path("/repo")
    with pytest.raises(SystemExit, match="Unsupported direct `invar` invocation"):
        invar_entrypoint._enforce_supported_invocation("/usr/local/bin/invar", ["guard"], repo_root)


def test_enforce_supported_invocation_allows_repo_venv_binary() -> None:
    """Repo-local venv invar binary is considered supported."""

    repo_root = Path("/repo")
    invar_entrypoint._enforce_supported_invocation("/repo/.venv/bin/invar", ["guard"], repo_root)


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


def test_run_guard_app_falls_back_to_uvx_when_guard_unavailable(monkeypatch) -> None:
    """Guard runner uses uvx fallback when invar package is unavailable."""

    called = {"uvx": False}

    original_import = __import__

    def _fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> object:
        if name == "invar.shell.commands.guard":
            raise ModuleNotFoundError(name="invar")
        return original_import(name, globals, locals, fromlist, level)

    def _fake_uvx(argv: Sequence[str]) -> None:
        called["uvx"] = True
        assert list(argv) == ["guard", "tests/unit/test_invar_entrypoint.py"]

    monkeypatch.setattr("builtins.__import__", _fake_import)
    monkeypatch.setattr(invar_entrypoint, "_invoke_uvx_invar_guard", _fake_uvx)

    invar_entrypoint._run_guard_app(["guard", "tests/unit/test_invar_entrypoint.py"])

    assert called == {"uvx": True}
