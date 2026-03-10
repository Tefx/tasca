"""Tests for startup policy wiring via ``usercustomize`` module."""

from __future__ import annotations

import importlib
import sys


def test_usercustomize_invokes_runtime_guard_contract(monkeypatch) -> None:
    """`usercustomize` forwards interpreter argv to runtime guard policy."""

    seen: dict[str, list[str]] = {}

    def _capture(argv: list[str]) -> None:
        seen["argv"] = argv

    from tasca.shell import invar_runtime_policy

    monkeypatch.setattr(invar_runtime_policy, "enforce_runtime_guard_contract", _capture)
    monkeypatch.setattr(sys, "argv", ["/repo/.venv/bin/invar", "guard"])
    sys.modules.pop("usercustomize", None)

    importlib.import_module("usercustomize")

    assert seen == {"argv": ["/repo/.venv/bin/invar", "guard"]}
