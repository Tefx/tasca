"""Tests for contract-enforced guard wrapper."""

from __future__ import annotations

import subprocess
import json
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
    assert "owner-controlled canonical evidence" in message.lower()
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

    calls: list[list[str]] = []

    def _fake_run(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        calls.append(command)
        if command == ["uv", "run", "--group", "dev", "invar", "guard"]:
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=payload, stderr=""
            )
        if command == ["./scripts/invar", "guard", "--all"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout='{"status":"passed","summary":{"files_checked":2}}',
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)
    assert guard_contract.run_guard_contract() == 0
    captured = capsys.readouterr()
    assert calls == [
        ["uv", "run", "--group", "dev", "invar", "guard"],
        ["./scripts/invar", "guard", "--all"],
    ]
    assert "non-canonical standalone signal" in captured.err
    assert "does not by itself determine gate closure" in captured.err
    assert "Canonical closure owner: guard-contract." in captured.err


def test_run_guard_contract_passes_nonzero_file_guard(monkeypatch) -> None:
    """Contract wrapper preserves successful meaningful guard output."""

    payload = '{"status":"passed","summary":{"files_checked":1}}'

    def _fake_run(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        if command == ["uv", "run", "--group", "dev", "invar", "guard"]:
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=payload, stderr=""
            )
        if command == ["./scripts/invar", "guard", "--all"]:
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=payload, stderr=""
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)
    assert guard_contract.run_guard_contract() == 0


def test_run_guard_contract_warning_heavy_errors_zero_still_passes(monkeypatch, capsys) -> None:
    """Warning-heavy output with errors=0 keeps Option B closure as PASS."""

    payload = '{"status":"passed","summary":{"files_checked":2,"errors":0,"warnings":37,"infos":0}}'

    def _fake_run(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        if command == ["uv", "run", "--group", "dev", "invar", "guard"]:
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=payload, stderr=""
            )
        if command == ["./scripts/invar", "guard", "--all"]:
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=payload, stderr=""
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.delenv("TASCA_FREEZE_HEAD", raising=False)
    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)
    assert guard_contract.run_guard_contract() == 0
    captured = capsys.readouterr()
    assert "canonical closure owner is `guard-contract`" in captured.err
    assert "informational evidence only" in captured.err


def test_run_guard_contract_fails_when_canonical_command_fails(monkeypatch) -> None:
    """Canonical owner command return code determines closure result."""

    def _fake_run(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        if command == ["uv", "run", "--group", "dev", "invar", "guard"]:
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="{}", stderr="")
        if command == ["./scripts/invar", "guard", "--all"]:
            return subprocess.CompletedProcess(
                args=command, returncode=7, stdout="", stderr="failed"
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)
    assert guard_contract.run_guard_contract() == 7


def test_run_guard_contract_writes_expected_artifacts(monkeypatch, tmp_path) -> None:
    """Canonical and raw evidence artifacts include streams and ownership."""

    payload = '{"status":"passed","summary":{"files_checked":1}}'

    def _fake_run(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=payload, stderr="")

    monkeypatch.setenv(guard_contract._ARTIFACT_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)

    assert guard_contract.run_guard_contract() == 0

    raw_artifact = tmp_path / guard_contract._ARTIFACTS["raw"]
    canonical_artifact = tmp_path / guard_contract._ARTIFACTS["canonical"]
    presence_artifact = tmp_path / guard_contract._ARTIFACTS["presence"]
    assert raw_artifact.exists()
    assert canonical_artifact.exists()
    assert presence_artifact.exists()

    raw_payload = raw_artifact.read_text(encoding="utf-8")
    assert '"owner_classification": "non-canonical"' in raw_payload
    assert '"command": "uv run --group dev invar guard"' in raw_payload
    assert (
        '"stdout": "{\\"status\\":\\"passed\\",\\"summary\\":{\\"files_checked\\":1}}"'
        in raw_payload
    )

    canonical_payload = canonical_artifact.read_text(encoding="utf-8")
    assert '"owner_classification": "canonical:guard-contract"' in canonical_payload
    assert '"command": "./scripts/invar guard --all"' in canonical_payload

    presence_payload = presence_artifact.read_text(encoding="utf-8")
    assert '"all_present": true' in presence_payload
    assert '"checks"' in presence_payload


def test_presence_artifact_keeps_raw_zero_file_pass_after_presence_validation(
    monkeypatch, tmp_path
) -> None:
    """Final presence artifact retains anti-loop signal after validation rewrite."""

    raw_payload = '{"status":"passed","summary":{"files_checked":0}}'
    canonical_payload = '{"status":"passed","summary":{"files_checked":2}}'

    def _fake_run(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        if command == ["uv", "run", "--group", "dev", "invar", "guard"]:
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=raw_payload, stderr=""
            )
        if command == ["./scripts/invar", "guard", "--all"]:
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=canonical_payload, stderr=""
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setenv(guard_contract._ARTIFACT_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)

    assert guard_contract.run_guard_contract() == 0

    payload = json.loads(
        (tmp_path / guard_contract._ARTIFACTS["presence"]).read_text(encoding="utf-8")
    )
    assert payload["raw_zero_file_pass"] is True
    assert payload["all_present"] is True
    assert payload["checks"]["raw"]["exists"] is True
    assert payload["checks"]["canonical"]["exists"] is True


def test_presence_artifact_keeps_raw_zero_file_pass_false_with_canonical_capture(
    monkeypatch, tmp_path
) -> None:
    """Canonical branch capture does not erase deterministic raw-zero=false proof."""

    payload = '{"status":"passed","summary":{"files_checked":3}}'

    def _fake_run(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=payload, stderr="")

    monkeypatch.setenv(guard_contract._ARTIFACT_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)

    assert guard_contract.run_guard_contract() == 0

    presence = json.loads(
        (tmp_path / guard_contract._ARTIFACTS["presence"]).read_text(encoding="utf-8")
    )
    assert "raw_zero_file_pass" in presence
    assert presence["raw_zero_file_pass"] is False
    assert presence["checks"]["canonical"]["non_empty"] is True


def test_run_guard_contract_fails_when_presence_artifact_missing(monkeypatch, tmp_path) -> None:
    """Missing artifacts after command execution fail closure immediately."""

    payload = '{"status":"passed","summary":{"files_checked":1}}'

    def _fake_run(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=payload, stderr="")

    original_write = guard_contract._write_artifact

    def _fake_write(path, payload_obj):
        original_write(path, payload_obj)
        if path.name == guard_contract._ARTIFACTS["presence"]:
            path.unlink()

    monkeypatch.setenv(guard_contract._ARTIFACT_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(guard_contract.subprocess, "run", _fake_run)
    monkeypatch.setattr(guard_contract, "_write_artifact", _fake_write)

    assert guard_contract.run_guard_contract() == 2


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
