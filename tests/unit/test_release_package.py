"""Release packaging contracts, including installed MCP runtime dependencies."""

from __future__ import annotations

import json
import subprocess
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

REPOSITORY = Path(__file__).parents[2]
VERSION = "0.1.33"
WHEEL_NAME = f"tasca-{VERSION}-py3-none-any.whl"


def run(command: list[str], *, cwd: Path = REPOSITORY) -> subprocess.CompletedProcess[str]:
    """Run a release subprocess with captured output for assertion diagnostics."""
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def test_release_metadata_is_aligned() -> None:
    """Python and Web package metadata identify the same release candidate."""
    pyproject = tomllib.loads((REPOSITORY / "pyproject.toml").read_text())
    web_package = json.loads((REPOSITORY / "web/package.json").read_text())
    package_source = (REPOSITORY / "src/tasca/__init__.py").read_text()

    assert pyproject["project"]["version"] == VERSION
    assert web_package["version"] == VERSION
    assert f'__version__ = "{VERSION}"' in package_source


def test_sdist_has_bounded_release_contents(tmp_path: Path) -> None:
    """A built source archive contains selected release inputs and no local state."""
    dist_dir = tmp_path / "sdist"
    build = run(["uv", "build", "--sdist", "--out-dir", str(dist_dir)])
    assert build.returncode == 0, build.stderr

    archive_path = dist_dir / f"tasca-{VERSION}.tar.gz"
    assert archive_path.is_file()
    with tarfile.open(archive_path) as archive:
        names = {
            member.name.split("/", 1)[1] for member in archive.getmembers() if "/" in member.name
        }
    assert {
        "pyproject.toml",
        "src/tasca/__init__.py",
        "scripts/gcp/viewer-auth-rollout.sh",
        "scripts/gcp/build-viewer-auth-rollback-bundle.sh",
        "scripts/gcp/verify_viewer_auth_remote.py",
        "scripts/gcp/seat-presence-forward-deploy.sh",
        "scripts/gcp/attachment-forward-deploy.sh",
        "scripts/gcp/verify_attachments_remote.py",
    } <= names
    assert "scripts/gcp/verify_remote_mcp.py" not in names
    forbidden_prefixes = (
        "web/node_modules/",
        ".vectl/",
        ".venv/",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        ".hypothesis/",
        ".invar/",
        "evidence/",
        "gate_reports/",
        "data/",
        "dist/",
    )
    assert "plan.yaml" not in names
    assert not any(name.startswith(forbidden_prefixes) for name in names)


def test_wheel_contains_tracked_spa_and_fresh_cli(tmp_path: Path) -> None:
    """Build, install, and invoke the wheel independently of the source tree."""
    dist_dir = tmp_path / "dist"
    build = run(["uv", "build", "--wheel", "--out-dir", str(dist_dir)])
    assert build.returncode == 0, build.stderr

    wheel = dist_dir / WHEEL_NAME
    assert wheel.is_file()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = archive.read(f"tasca-{VERSION}.dist-info/METADATA").decode()
        assert "tasca/web/dist/index.html" in names
        assert any(name.startswith("tasca/web/dist/assets/") for name in names)
        assert f"Version: {VERSION}" in metadata
        # Direct imports must remain runtime dependencies in the published wheel,
        # even when upstream packages stop depending on the same distributions.
        from packaging.requirements import Requirement

        requirements = {
            requirement.name: requirement
            for value in Parser().parsestr(metadata).get_all("Requires-Dist", [])
            if (requirement := Requirement(value)).marker is None
        }
        assert "httpx" in requirements
        assert requirements["httpx"].specifier.contains("0.28.1")
        assert "fastmcp" in requirements
        assert not requirements["fastmcp"].specifier.contains("4.1.0")
        seat_source = (REPOSITORY / "src/tasca/core/domain/seat.py").read_bytes()
        wheel_seat_source = archive.read("tasca/core/domain/seat.py")
        assert wheel_seat_source == seat_source

    environment = tmp_path / "fresh-wheel"
    create_environment = run(["uv", "venv", str(environment)])
    assert create_environment.returncode == 0, create_environment.stderr
    python = environment / "bin/python"
    install = run(["uv", "pip", "install", "--python", str(python), str(wheel)])
    assert install.returncode == 0, install.stderr
    version = run(
        [str(python), "-c", "from importlib.metadata import version; print(version('tasca'))"]
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == VERSION
    help_result = run([str(environment / "bin/tasca"), "--help"])
    assert help_result.returncode == 0, help_result.stderr
    assert "Tasca - A discussion table service" in help_result.stdout
    static_shell = run(
        [
            str(python),
            "-c",
            """
import re
from fastapi.testclient import TestClient
from tasca.shell.api.app import create_app

client = TestClient(create_app())
shell = client.get("/")
assert shell.status_code == 200
asset = re.search(r'(?:src|href)=\"(/assets/[^\"?#]+)\"', shell.text)
assert asset is not None
assert client.get(asset.group(1)).status_code == 200
""",
        ],
        cwd=tmp_path,
    )
    assert static_shell.returncode == 0, static_shell.stderr

    # Exercise the installed entry point, with no source checkout or dev extras.
    # The SDK performs initialize and notifications/initialized before listing.
    mcp_smoke = run(
        [
            str(python),
            "-c",
            """
import asyncio
import os
import sys
from importlib.metadata import version
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "patron_register", "patron_get", "table_create", "table_join", "table_get",
    "table_list", "table_delete_batch", "table_export", "attachment_get",
    "table_say", "table_listen", "table_control", "table_update", "table_wait",
    "seat_heartbeat", "seat_list", "connect", "connection_status",
}

async def smoke():
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("TASCA_") and key != "PYTHONPATH"}
    env["TASCA_DB_PATH"] = str(Path.cwd() / "mcp-smoke.db")
    params = StdioServerParameters(
        command=str(Path(sys.executable).with_name("tasca")),
        args=["mcp"], env=env, cwd=str(Path.cwd()),
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "tasca"
            names = set()
            cursor = None
            while True:
                page = await session.list_tools(cursor=cursor)
                names.update(tool.name for tool in page.tools)
                cursor = page.nextCursor
                if cursor is None:
                    break
            assert names == EXPECTED_TOOLS, names
    print(f"initialize OK; tools/list: {len(names)} tools; "
          f"httpx={version('httpx')}; fastmcp={version('fastmcp')}; mcp={version('mcp')}")

asyncio.run(asyncio.wait_for(smoke(), timeout=20))
""",
        ],
        cwd=tmp_path,
    )
    assert mcp_smoke.returncode == 0, mcp_smoke.stderr
