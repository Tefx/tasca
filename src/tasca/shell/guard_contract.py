"""Repo-owned contract wrapper for guard command enforcement.

This module exists to make zero-file changed-mode PASS outcomes fail closed
with actionable guidance, even when the resolved `invar` executable is not the
repo-owned shim.
"""

from __future__ import annotations

import json
import subprocess
import sys


# @invar:allow shell_result: message helper for guard contract CLI diagnostics
def _build_zero_file_contract_message() -> str:
    """Return actionable guidance for blocked zero-file PASS behavior."""

    return (
        "guard contract violation: raw `uv run --group dev invar guard` returned PASS with "
        "files_checked=0.\n"
        "Raw `uv run --group dev invar guard` is a non-canonical standalone signal and "
        "must be evaluated only through this contract check.\n"
        "Canonical commands for gate closure:\n"
        "  - guard-contract\n"
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


# @invar:allow shell_result: CLI orchestration wrapper returns process status code
# @shell_complexity: command output relay + contract branch handling is intentional
def run_guard_contract() -> int:
    """Execute blocked command and fail on ambiguous zero-file PASS output."""

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
        return 2
    return 0


def main() -> None:
    """CLI entrypoint for contract-enforced guard invocation."""

    raise SystemExit(run_guard_contract())
