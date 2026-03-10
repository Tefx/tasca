"""Runtime policy checks for `invar` executable invocation.

Source: guard_followup2_cleanup.gate-zero-file-policy-fix requires actionable
failure for ambiguous guard invocation paths that can silently report
`files_checked=0` as PASS.
"""

from __future__ import annotations

import shutil
from pathlib import Path


# @invar:allow shell_result: startup guardrail predicate for invar runtime policy
def _is_invar_executable(argv0: str) -> bool:
    """Return True when process executable appears to be `invar`."""

    return Path(argv0).name == "invar"


# @invar:allow shell_result: startup helper reads generated script text
def _is_invar_tools_guard_entrypoint(executable: Path) -> bool:
    """Return True when executable is generated from invar-tools guard app."""

    try:
        content = executable.read_text(encoding="utf-8")
    except OSError:
        return False
    return "from invar.shell.commands.guard import app" in content


# @invar:allow shell_result: startup helper resolves executable path token
def _resolve_invar_executable(argv0: str) -> Path | None:
    """Resolve argv0 to the real executable path when possible."""

    candidate = Path(argv0)
    if candidate.is_absolute():
        return candidate

    resolved = shutil.which(argv0)
    if resolved is None:
        return None
    return Path(resolved)


# @invar:allow shell_result: startup policy message formatter
def _ambiguous_guard_token_message(executable: Path) -> str:
    """Build actionable error for ambiguous `invar guard` invocation."""

    return (
        "Unsupported invocation: `invar guard` is ambiguous for this executable.\n"
        f"Resolved executable: {executable.resolve()}\n"
        "\n"
        "Why this fails:\n"
        "- This `invar` binary already executes guard directly.\n"
        "- Passing literal `guard` is interpreted as a path token, which can\n"
        "  silently produce PASS with files_checked=0.\n"
        "\n"
        "Use one of these supported commands:\n"
        "  - uv run --group dev invar --all\n"
        "  - uv run --group dev invar <path>\n"
        "  - ./scripts/invar guard --all\n"
        "  - ./scripts/invar guard <path>"
    )


def enforce_runtime_guard_contract(argv: list[str]) -> None:
    """Enforce strict contract for actually-invoked invar-tools entrypoint.

    Source: blocker command `uv run --group dev invar guard` should not silently
    pass with zero checked files due to entrypoint argument ambiguity.
    """

    if len(argv) < 2:
        return
    argv0 = argv[0]
    if not _is_invar_executable(argv0):
        return

    executable = _resolve_invar_executable(argv0)
    if executable is None:
        return

    if not _is_invar_tools_guard_entrypoint(executable):
        return

    if argv[1] != "guard":
        return

    raise SystemExit(_ambiguous_guard_token_message(executable))
