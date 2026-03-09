"""Compatibility entrypoint for `invar` command.

This wrapper preserves `invar guard ...` usage in environments where the
installed `invar-tools` package is missing `invar.shell.commands.hooks`.
"""

from __future__ import annotations

import importlib
import sys
import types


def _install_missing_hooks_stub() -> None:
    """Install a minimal hooks module when invar-tools lacks it.

    Source: observed runtime failure `ModuleNotFoundError:
    invar.shell.commands.hooks` when importing `invar.shell.commands.guard`.
    """
    try:
        importlib.import_module("invar.shell.commands.hooks")
        return
    except ModuleNotFoundError as error:
        if error.name != "invar.shell.commands.hooks":
            raise

    import typer

    hooks_module = types.ModuleType("invar.shell.commands.hooks")
    hooks_app = typer.Typer(help="Compatibility placeholder when hooks command is unavailable.")
    setattr(hooks_module, "app", hooks_app)
    sys.modules["invar.shell.commands.hooks"] = hooks_module


def main() -> None:
    """Dispatch to the upstream invar guard Typer app."""
    _install_missing_hooks_stub()
    from invar.shell.commands.guard import app

    app()
