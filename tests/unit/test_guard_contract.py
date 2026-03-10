"""Tests for contract-enforced guard wrapper."""

from __future__ import annotations

import subprocess
from typing import Any

from tasca.shell import guard_contract


def test_is_zero_file_pass_detects_blocked_payload() -> None:
    """PASS+files_checked=0 payloads are treated as contract violations."""

    payload = '{"status":"passed","summary":{"files_checked":0}}'
    assert guard_contract._is_zero_file_pass(payload) is True


def test_build_zero_file_contract_message_enforces_canonical_commands() -> None:
    """Guidance encodes Option B closure ownership and raw signal semantics."""

    message = guard_contract._build_zero_file_contract_message()
    assert "Canonical closure owner: guard-contract." in message
    assert "./scripts/invar guard --all" in message
    assert "informational evidence" in message.lower()
    assert "non-canonical standalone signal" in message
    assert "does not by itself determine gate closure" in message
    assert "uv run --group dev invar guard" in message


def test_is_zero_file_pass_ignores_nonzero_file_payload() -> None:
    """Non-zero checked file payloads are accepted by the contract."""

    payload = '{"status":"passed","summary":{"files_checked":3}}'
    assert guard_contract._is_zero_file_pass(payload) is False


def test_run_guard_contract_warns_on_zero_file_pass(monkeypatch, capsys) -> None:
    """Contract wrapper warns but keeps canonical closure interpretation."""

    payload = '{"status":"passed","summary":{"files_checked":0}}'

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["uv"], returncode=0, stdout=payload, stderr="")

    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)
    assert guard_contract.run_guard_contract() == 0
    captured = capsys.readouterr()
    assert "non-canonical standalone signal" in captured.err
    assert "does not by itself determine gate closure" in captured.err
    assert "Canonical closure owner: guard-contract." in captured.err
    assert "informational evidence" in captured.err.lower()


def test_run_guard_contract_passes_nonzero_file_guard(monkeypatch) -> None:
    """Contract wrapper preserves successful meaningful guard output."""

    payload = '{"status":"passed","summary":{"files_checked":1}}'

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["uv"], returncode=0, stdout=payload, stderr="")

    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)
    assert guard_contract.run_guard_contract() == 0


def test_run_guard_contract_warning_heavy_errors_zero_still_passes(monkeypatch, capsys) -> None:
    """Warning-heavy output with errors=0 keeps Option B closure as PASS."""

    payload = '{"status":"passed","summary":{"files_checked":2,"errors":0,"warnings":37,"infos":0}}'

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["uv"], returncode=0, stdout=payload, stderr="")

    monkeypatch.delenv("TASCA_FREEZE_HEAD", raising=False)
    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)
    assert guard_contract.run_guard_contract() == 0
    captured = capsys.readouterr()
    assert "canonical closure owner is `guard-contract`" in captured.err
    assert "informational evidence only" in captured.err


def test_is_plan_only_drift_accepts_plan_and_vectl_paths() -> None:
    """Freeze drift policy accepts plan-only changes."""

    assert guard_contract._is_plan_only_drift(["plan.yaml", ".git/vectl/claims.json"]) is True


def test_is_plan_only_drift_rejects_non_plan_paths() -> None:
    """Freeze drift policy rejects code drift after freeze."""

    assert (
        guard_contract._is_plan_only_drift(
            ["plan.yaml", ".git/vectl/claims.json", "src/tasca/shell/guard_contract.py"]
        )
        is False
    )


def test_run_guard_contract_accepts_plan_only_freeze_drift(monkeypatch, capsys) -> None:
    """Freeze-head mismatch is valid when drift is strictly plan-only."""

    guard_payload = (
        '{"status":"passed","summary":{"files_checked":1,"errors":0,"warnings":12,"infos":0}}'
    )
    freeze_drift = "plan.yaml\n.git/vectl/claims.json\n"

    def _fake_run(*args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        if command[:3] == ["git", "diff", "--name-only"]:
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=freeze_drift, stderr=""
            )
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout=guard_payload, stderr=""
        )

    monkeypatch.setenv("TASCA_FREEZE_HEAD", "abc123")
    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)

    assert guard_contract.run_guard_contract() == 0
    captured = capsys.readouterr()
    assert "plan-only and accepted" in captured.err
    assert "changed files: plan.yaml, .git/vectl/claims.json" in captured.err


def test_run_guard_contract_rejects_non_plan_freeze_drift(monkeypatch, capsys) -> None:
    """Freeze-head mismatch is invalid when drift includes non-plan files."""

    guard_payload = (
        '{"status":"passed","summary":{"files_checked":1,"errors":0,"warnings":0,"infos":0}}'
    )
    freeze_drift = "plan.yaml\nsrc/tasca/shell/guard_contract.py\n"

    def _fake_run(*args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        if command[:3] == ["git", "diff", "--name-only"]:
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=freeze_drift, stderr=""
            )
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout=guard_payload, stderr=""
        )

    monkeypatch.setenv("TASCA_FREEZE_HEAD", "abc123")
    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)

    assert guard_contract.run_guard_contract() == 2
    captured = capsys.readouterr()
    assert "invalid post-freeze drift outside plan-only scope" in captured.err
    assert "allowed: plan.yaml and .git/vectl/** only" in captured.err
