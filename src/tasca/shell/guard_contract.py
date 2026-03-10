"""Repo-owned contract wrapper for deterministic guard closure.

Source: `guard_followup2_cleanup.gate-deterministic-closure-and-freeze-intent-fix`.

Chosen closure rule is Option B:
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


_CANONICAL_CLOSURE_OWNER = "guard-contract"
_INFORMATIONAL_GUARD_EVIDENCE = "./scripts/invar guard --all"


# @invar:allow shell_result: message helper for guard contract CLI diagnostics
def _build_zero_file_contract_message() -> str:
    """Return guidance for non-canonical zero-file PASS behavior."""

    return (
        "guard contract note: raw `uv run --group dev invar guard` returned PASS with "
        "files_checked=0.\n"
        "Raw `uv run --group dev invar guard` is a non-canonical standalone signal and "
        "does not by itself determine gate closure.\n"
        "Canonical closure owner: guard-contract.\n"
        "Informational evidence command (non-owner):\n"
        "  - ./scripts/invar guard --all"
    )


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
    """Execute guard command and enforce deterministic Option B closure policy."""

    command = ["uv", "run", "--group", "dev", "invar", "guard"]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        return completed.returncode
    if _is_zero_file_pass(completed.stdout):
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
