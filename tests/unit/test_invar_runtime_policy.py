"""Tests for startup runtime policy on `invar` executable."""

from __future__ import annotations

from pathlib import Path

import pytest

from tasca.shell import invar_runtime_policy


def test_runtime_contract_allows_supported_invar_tools_call_without_guard_token(
    tmp_path: Path,
) -> None:
    """Supported invocation (`invar --all`) bypasses ambiguity failure."""

    executable = tmp_path / "invar"
    executable.write_text("from invar.shell.commands.guard import app\n", encoding="utf-8")

    invar_runtime_policy.enforce_runtime_guard_contract([str(executable), "--all"])


def test_runtime_contract_rejects_ambiguous_guard_token_for_invar_tools(
    tmp_path: Path,
) -> None:
    """`invar guard` fails fast for repo venv-style executable path."""

    executable = tmp_path / ".venv" / "bin" / "invar"
    executable.parent.mkdir(parents=True)
    executable.write_text("from invar.shell.commands.guard import app\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="Unsupported invocation: `invar guard` is ambiguous"):
        invar_runtime_policy.enforce_runtime_guard_contract([str(executable), "guard"])


def test_runtime_contract_rejects_ambiguous_guard_token_for_resolved_invar_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`argv0=invar` is resolved through PATH and still enforces guard contract."""

    executable = tmp_path / "invar"
    executable.write_text("from invar.shell.commands.guard import app\n", encoding="utf-8")
    monkeypatch.setattr(invar_runtime_policy.shutil, "which", lambda _: str(executable))

    with pytest.raises(SystemExit, match="Unsupported invocation: `invar guard` is ambiguous"):
        invar_runtime_policy.enforce_runtime_guard_contract(["invar", "guard"])


def test_runtime_contract_skips_when_invar_name_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unresolvable `argv0=invar` skips ambiguous-entrypoint check."""

    monkeypatch.setattr(invar_runtime_policy.shutil, "which", lambda _: None)
    invar_runtime_policy.enforce_runtime_guard_contract(["invar", "guard"])
