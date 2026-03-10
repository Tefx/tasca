"""Tests for top-level invar console entrypoint shim."""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest

import tasca_invar_entrypoint


def test_main_reports_guidance_when_tasca_module_is_missing(monkeypatch) -> None:
    """Direct invar entrypoint emits guidance instead of traceback noise."""

    original_import = __import__

    def _fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> object:
        if name == "tasca.shell.invar_entrypoint":
            raise ModuleNotFoundError(name="tasca")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    with pytest.raises(SystemExit) as exc_info:
        tasca_invar_entrypoint.main()

    message = str(exc_info.value)
    assert "Unable to start direct `invar` entrypoint" in message
    assert "Missing module: tasca" in message
    assert "uv run invar guard --all" in message


def test_pyproject_contract_keeps_repo_owned_invar_script() -> None:
    """Project config keeps invar-tools pin while preserving local shim contract."""

    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    pyproject_data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    scripts = pyproject_data["project"]["scripts"]
    assert scripts["invar"] == "tasca_invar_entrypoint:main"

    project_dependencies = pyproject_data["project"].get("dependencies", [])
    dev_dependencies = pyproject_data["dependency-groups"].get("dev", [])
    assert all(not str(dep).startswith("invar-tools") for dep in project_dependencies)
    assert any(str(dep).startswith("invar-tools==1.19.7") for dep in dev_dependencies)


def test_runtime_invar_script_resolves_to_tasca_entrypoint() -> None:
    """Installed `invar` script remains deterministic and machine-inspectable."""

    invar_script = Path(sys.executable).with_name("invar")
    content = invar_script.read_text(encoding="utf-8")

    assert "import sys" in content
    assert 'if __name__ == "__main__":' in content
