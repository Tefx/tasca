"""Compatibility entrypoint for `invar` command.

This wrapper preserves `invar guard ...` usage in environments where the
installed `invar-tools` package is missing `invar.shell.commands.hooks`.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import types
from collections.abc import Sequence
from pathlib import Path


def _install_missing_hooks_stub() -> None:
    """Install a minimal hooks module when invar-tools lacks it.

    Source: observed runtime failure `ModuleNotFoundError:
    invar.shell.commands.hooks` when importing `invar.shell.commands.guard`.
    """
    try:
        importlib.import_module("invar.shell.commands.hooks")
        return
    except ModuleNotFoundError as error:
        if error.name == "invar":
            return
        if error.name != "invar.shell.commands.hooks":
            raise

    import typer

    hooks_module = types.ModuleType("invar.shell.commands.hooks")
    hooks_app = typer.Typer(help="Compatibility placeholder when hooks command is unavailable.")
    hooks_module.__dict__["app"] = hooks_app
    sys.modules["invar.shell.commands.hooks"] = hooks_module


# @invar:allow shell_result: CLI argument predicate helper for entrypoint policy
def _is_guard_invocation(argv: Sequence[str]) -> bool:
    """Return True when argv targets `invar guard` command."""
    return len(argv) > 0 and argv[0] == "guard"


# @invar:allow shell_result: CLI argument predicate helper for entrypoint policy
def _has_all_flag(argv: Sequence[str]) -> bool:
    """Return True when guard invocation explicitly requests `--all`."""
    return "--all" in argv


# @invar:allow shell_result: CLI argument predicate helper for entrypoint policy
def _has_explicit_target(argv: Sequence[str]) -> bool:
    """Return True when guard command includes a positional path target."""
    return any(not token.startswith("-") for token in argv[1:])


# @invar:allow shell_result: entrypoint helper reads git status for guard policy
def _list_changed_python_files(repo_root: Path) -> set[str]:
    """Collect changed Python files from tracked and untracked git state.

    Source: `invar guard` defaults to changed-only mode and can emit
    files_checked=0 with a passing status, which is easy to misread as full
    verification.
    """

    tracked = subprocess.run(
        ["git", "status", "--porcelain", "--", "*.py"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    changed: set[str] = set()
    for line in tracked.stdout.splitlines():
        path = line[3:].strip()
        if path:
            changed.add(path)
    return changed


def _enforce_changed_files_policy(argv: Sequence[str], repo_root: Path) -> None:
    """Fail fast when `invar guard` runs with no changed Python files.

    Policy source: step requirement
    `guard_followup2_cleanup.storage-guard-verify-retest-fix-entrypoint-and-zero-file-policy`.
    The policy prevents PASS+files_checked=0 from being interpreted as
    meaningful verification.
    """

    if not _is_guard_invocation(argv):
        return
    if _has_all_flag(argv):
        return
    if _has_explicit_target(argv):
        return

    try:
        changed_python_files = _list_changed_python_files(repo_root)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return

    if changed_python_files:
        return

    message = (
        "invar entrypoint policy: no changed Python files found for "
        "`invar guard` changed-mode. Use `invar guard --all` for meaningful "
        "verification."
    )
    raise SystemExit(message)


def _invoke_uvx_invar_guard(argv: Sequence[str]) -> None:
    """Fallback to uvx invar-tools when importable invar package is missing.

    Source: direct installed entrypoint may run in an environment that has the
    tasca console script but not an importable `invar` package.
    """

    command = ["uvx", "invar-tools", *argv]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _run_guard_app(argv: Sequence[str]) -> None:
    """Run invar guard app via import, with uvx fallback when unavailable."""

    try:
        from invar.shell.commands.guard import app
    except ModuleNotFoundError as error:
        if error.name not in {
            "invar",
            "invar.shell",
            "invar.shell.commands",
            "invar.shell.commands.guard",
        }:
            raise
        _invoke_uvx_invar_guard(argv)
        return

    app()


def main() -> None:
    """Dispatch to the upstream invar guard Typer app."""
    argv = sys.argv[1:]
    _enforce_changed_files_policy(argv, Path.cwd())
    _install_missing_hooks_stub()
    _run_guard_app(argv)
