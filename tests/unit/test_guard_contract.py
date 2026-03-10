"""Tests for contract-enforced guard wrapper."""

from __future__ import annotations

import subprocess

from tasca.shell import guard_contract


def test_is_zero_file_pass_detects_blocked_payload() -> None:
    """PASS+files_checked=0 payloads are treated as contract violations."""

    payload = '{"status":"passed","summary":{"files_checked":0}}'
    assert guard_contract._is_zero_file_pass(payload) is True


def test_build_zero_file_contract_message_enforces_canonical_commands() -> None:
    """Guidance encodes canonical closure commands and non-canonical raw signal."""

    message = guard_contract._build_zero_file_contract_message()
    assert "guard-contract" in message
    assert "./scripts/invar guard --all" in message
    assert "non-canonical standalone signal" in message
    assert "uv run --group dev invar guard" in message


def test_is_zero_file_pass_ignores_nonzero_file_payload() -> None:
    """Non-zero checked file payloads are accepted by the contract."""

    payload = '{"status":"passed","summary":{"files_checked":3}}'
    assert guard_contract._is_zero_file_pass(payload) is False


def test_run_guard_contract_fails_on_zero_file_pass(monkeypatch, capsys) -> None:
    """Contract wrapper exits non-zero when blocked behavior is observed."""

    payload = '{"status":"passed","summary":{"files_checked":0}}'

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["uv"], returncode=0, stdout=payload, stderr="")

    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)
    assert guard_contract.run_guard_contract() == 2
    captured = capsys.readouterr()
    assert "non-canonical standalone signal" in captured.err
    assert "guard-contract" in captured.err
    assert "./scripts/invar guard --all" in captured.err


def test_run_guard_contract_passes_nonzero_file_guard(monkeypatch) -> None:
    """Contract wrapper preserves successful meaningful guard output."""

    payload = '{"status":"passed","summary":{"files_checked":1}}'

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["uv"], returncode=0, stdout=payload, stderr="")

    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)
    assert guard_contract.run_guard_contract() == 0
