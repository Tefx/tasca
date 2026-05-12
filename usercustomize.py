"""Interpreter startup customization for Tasca local tooling."""

from __future__ import annotations

import sys

from tasca.shell.invar_runtime_policy import enforce_runtime_guard_contract

enforce_runtime_guard_contract(sys.argv)
