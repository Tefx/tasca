"""Startup policy hook for Invar runtime command wiring.

The module is intentionally small and side-effect free: ``usercustomize`` owns
interpreter-startup invocation, while this shell module owns the import path and
callable contract that tests and local runtime policy patching depend on.
"""

from __future__ import annotations


# @invar:allow shell_result: Startup policy hook mutates no state and returns None by convention.
# @shell_orchestration: Startup integration policy is invoked by usercustomize at interpreter startup.
def enforce_runtime_guard_contract(argv: list[str]) -> None:
    """Validate startup-time Invar guard policy inputs.

    The current policy is permissive because guard enforcement is performed by
    the ``invar`` command itself.  Keeping this callable in the shell layer gives
    ``usercustomize`` a stable integration point without adding import-time I/O.

    Args:
        argv: Interpreter argument vector forwarded by ``usercustomize``.

    Example:
        >>> enforce_runtime_guard_contract(["/repo/.venv/bin/invar", "guard"])
    """
    if not isinstance(argv, list):
        raise TypeError("argv must be a list[str]")
