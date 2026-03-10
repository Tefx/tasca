"""Tests for startup runtime policy on `invar` executable."""

from __future__ import annotations

from pathlib import Path

import pytest

from tasca.shell.invar_runtime_policy import enforce_runtime_guard_contract


def test_runtime_contract_allows_supported_invar_tools_call_without_guard_token(
    tmp_path: Path,
) -> None:
    """Supported invocation (`invar --all`) bypasses ambiguity failure."""

    executable = tmp_path / "invar"
    executable.write_text("from invar.shell.commands.guard import app\n", encoding="utf-8")

    enforce_runtime_guard_contract([str(executable), "--all"])


def test_runtime_contract_rejects_ambiguous_guard_token_for_invar_tools(
    tmp_path: Path,
) -> None:
    """`invar guard` fails fast with actionable guidance."""

    executable = tmp_path / "invar"
    executable.write_text("from invar.shell.commands.guard import app\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="Unsupported invocation: `invar guard` is ambiguous"):
        enforce_runtime_guard_contract([str(executable), "guard"])
