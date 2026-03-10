"""Repo-owned contract wrapper for deterministic guard closure.

Source: `guard_followup2_cleanup.gate-fix-single-closure-owner-and-canonical-evidence`.

Chosen closure rule:
- Canonical closure owner: `guard-contract` only.
- `./scripts/invar guard --all` remains informational evidence, not closure owner.

Freeze-intent rule is enforced when `TASCA_FREEZE_HEAD` is set:
- plan-only drift (`plan.yaml`, `.git/vectl/**`) is acceptable.
- any broader drift invalidates freeze and requires refresh.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


_CANONICAL_CLOSURE_OWNER = "guard-contract"
_INFORMATIONAL_GUARD_EVIDENCE = "./scripts/invar guard --all"
_RAW_NON_CANONICAL_COMMAND = "uv run --group dev invar guard"
_CANONICAL_COMMAND = "./scripts/invar guard --all"
_ARTIFACT_DIR_ENV = "TASCA_GUARD_CONTRACT_ARTIFACT_DIR"
_DEFAULT_ARTIFACT_DIR = ".artifacts/guard-contract"
_ARTIFACTS = {
    "canonical": "canonical-guard.json",
    "raw": "raw-guard.json",
    "presence": "presence-check.json",
}


# @invar:allow shell_result: message helper for guard contract CLI diagnostics
def _build_zero_file_contract_message() -> str:
    """Return guidance for non-canonical zero-file PASS behavior."""

    return (
        "guard contract note: raw `uv run --group dev invar guard` returned PASS with "
        "files_checked=0.\n"
        "Raw `uv run --group dev invar guard` is a non-canonical standalone signal and "
        "does not by itself determine gate closure.\n"
        "Canonical closure owner: guard-contract.\n"
        "Owner-controlled canonical evidence command:\n"
        "  - ./scripts/invar guard --all"
    )


# @invar:allow shell_result: artifact helper computes deterministic artifact location
def _artifact_dir() -> Path:
    """Return deterministic artifact directory for guard-contract runs."""

    configured = os.environ.get(_ARTIFACT_DIR_ENV)
    if configured:
        return Path(configured)
    return Path(_DEFAULT_ARTIFACT_DIR)


# @invar:allow shell_result: subprocess adapter captures command line plus streams
def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run command and return captured process output."""

    return subprocess.run(command, check=False, capture_output=True, text=True)


# @invar:allow shell_result: artifact helper executes atomic write with readback validation
def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON artifact atomically and verify readback parity."""

    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(serialized)
    try:
        temp_path.replace(path)
        readback = path.read_text(encoding="utf-8")
    finally:
        if temp_path.exists():
            temp_path.unlink()
    if readback != serialized:
        raise RuntimeError(f"artifact write mismatch for {path}")


# @invar:allow shell_result: evidence helper persists process output with owner classification
def _persist_command_artifact(
    *,
    path: Path,
    owner_classification: str,
    command: list[str],
    result: subprocess.CompletedProcess[str],
) -> None:
    """Persist a command-run artifact containing command and output streams."""

    _write_artifact(
        path,
        {
            "owner_classification": owner_classification,
            "command": " ".join(command),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    )


# @invar:allow shell_result: evidence helper validates required artifact file presence
def _validate_artifact_presence(artifact_dir: Path) -> tuple[bool, dict[str, Any]]:
    """Return (ok, payload) describing required artifact presence checks."""

    required = {role: artifact_dir / name for role, name in _ARTIFACTS.items()}
    checks = {
        role: {
            "path": str(path),
            "exists": path.exists(),
            "non_empty": path.exists() and path.stat().st_size > 0,
        }
        for role, path in required.items()
    }
    ok = all(item["exists"] and item["non_empty"] for item in checks.values())
    return ok, {"checks": checks, "all_present": ok}


# @invar:allow shell_result: JSON predicate helper for guard contract output parsing
def _is_zero_file_pass(payload: str) -> bool:
    """Return True when JSON payload reports PASS with zero checked files."""

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return False
    summary = parsed.get("summary")
    if not isinstance(summary, dict):
        return False
    return parsed.get("status") == "passed" and summary.get("files_checked") == 0


# @invar:allow shell_result: freeze-drift policy helper for deterministic gate evidence
def _is_plan_only_drift(paths: list[str]) -> bool:
    """Return True when all changed paths are plan-only freeze drift.

    Source: post-freeze drift policy from
    `guard_followup2_cleanup.gate-deterministic-closure-and-freeze-intent-fix`.
    """

    allowed_prefix = ".git/vectl/"
    for path in paths:
        if path == "plan.yaml":
            continue
        if path.startswith(allowed_prefix):
            continue
        return False
    return True


# @invar:allow shell_result: git diff helper for freeze drift evidence
def _list_post_freeze_changed_paths(freeze_head: str) -> list[str]:
    """List post-freeze changed paths between freeze head and current HEAD."""

    completed = subprocess.run(
        ["git", "diff", "--name-only", f"{freeze_head}..HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(f"unable to inspect post-freeze drift from {freeze_head}: {stderr}")

    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


# @invar:allow shell_result: freeze intent policy evaluation helper
def _evaluate_freeze_intent(freeze_head: str) -> tuple[bool, str]:
    """Evaluate freeze intent policy and return (valid, evidence message)."""

    changed_paths = _list_post_freeze_changed_paths(freeze_head)
    if not changed_paths:
        return True, f"freeze-intent: no post-freeze drift detected from {freeze_head}."

    file_list = ", ".join(changed_paths)
    if _is_plan_only_drift(changed_paths):
        return (
            True,
            "freeze-intent: post-freeze drift is plan-only and accepted. "
            f"changed files: {file_list}. reason: plan mutations in plan.yaml/.git/vectl/**.",
        )

    return (
        False,
        "freeze-intent: invalid post-freeze drift outside plan-only scope. "
        f"changed files: {file_list}. allowed: plan.yaml and .git/vectl/** only.",
    )


# @invar:allow shell_result: CLI orchestration wrapper returns process status code
# @shell_complexity: command output relay + contract branch handling is intentional
def run_guard_contract() -> int:
    """Execute guard command and enforce deterministic closure-owner policy."""

    raw_command = ["uv", "run", "--group", "dev", "invar", "guard"]
    canonical_command = ["./scripts/invar", "guard", "--all"]
    artifact_dir = _artifact_dir()

    raw_completed = _run_command(raw_command)
    canonical_completed = _run_command(canonical_command)

    if raw_completed.stdout:
        print(raw_completed.stdout, end="")
    if raw_completed.stderr:
        print(raw_completed.stderr, end="", file=sys.stderr)
    if canonical_completed.stdout:
        print(canonical_completed.stdout, end="")
    if canonical_completed.stderr:
        print(canonical_completed.stderr, end="", file=sys.stderr)

    try:
        _persist_command_artifact(
            path=artifact_dir / _ARTIFACTS["raw"],
            owner_classification="non-canonical",
            command=raw_command,
            result=raw_completed,
        )
        _persist_command_artifact(
            path=artifact_dir / _ARTIFACTS["canonical"],
            owner_classification=f"canonical:{_CANONICAL_CLOSURE_OWNER}",
            command=canonical_command,
            result=canonical_completed,
        )
        _write_artifact(
            artifact_dir / _ARTIFACTS["presence"],
            {
                "owner": _CANONICAL_CLOSURE_OWNER,
                "raw_command": _RAW_NON_CANONICAL_COMMAND,
                "canonical_command": _CANONICAL_COMMAND,
                "raw_zero_file_pass": _is_zero_file_pass(raw_completed.stdout),
            },
        )
    except RuntimeError as error:
        print(f"guard contract evidence error: {error}", file=sys.stderr)
        return 2

    artifacts_ok, presence_payload = _validate_artifact_presence(artifact_dir)
    try:
        _write_artifact(artifact_dir / _ARTIFACTS["presence"], presence_payload)
    except RuntimeError as error:
        print(f"guard contract evidence error: {error}", file=sys.stderr)
        return 2
    if not artifacts_ok:
        print("guard contract evidence error: missing required artifact files.", file=sys.stderr)
        return 2

    if canonical_completed.returncode != 0:
        return canonical_completed.returncode

    if _is_zero_file_pass(raw_completed.stdout):
        print(_build_zero_file_contract_message(), file=sys.stderr)
    else:
        print(
            "guard contract note: canonical closure owner is "
            f"`{_CANONICAL_CLOSURE_OWNER}`; `{_INFORMATIONAL_GUARD_EVIDENCE}` is informational evidence only.",
            file=sys.stderr,
        )

    freeze_head = os.environ.get("TASCA_FREEZE_HEAD")
    if freeze_head:
        try:
            freeze_valid, freeze_message = _evaluate_freeze_intent(freeze_head)
        except RuntimeError as error:
            print(f"freeze-intent: {error}", file=sys.stderr)
            return 2
        print(freeze_message, file=sys.stderr)
        if not freeze_valid:
            return 2
    return 0


def main() -> None:
    """CLI entrypoint for contract-enforced guard invocation."""

    raise SystemExit(run_guard_contract())
