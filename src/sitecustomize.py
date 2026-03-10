"""Runtime hook for startup policy checks.

This module is imported automatically by Python's site machinery when present
on sys.path.
"""

from __future__ import annotations

import sys

from tasca.shell.invar_runtime_policy import enforce_runtime_guard_contract


enforce_runtime_guard_contract(sys.argv)
