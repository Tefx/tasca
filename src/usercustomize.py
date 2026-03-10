"""Runtime hook for `invar` startup policy checks.

Python imports ``usercustomize`` after ``sitecustomize`` during interpreter
startup when present on ``sys.path``.
"""

from __future__ import annotations

import sys

from tasca.shell.invar_runtime_policy import enforce_runtime_guard_contract


enforce_runtime_guard_contract(sys.argv)
