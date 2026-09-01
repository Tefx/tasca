"""Offline fixture tests for remote rollback state and SQLite placement contracts."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parents[3]
REMOTE = REPOSITORY / "scripts/gcp/viewer-auth-remote.sh"


def write_fake_commands(directory: Path) -> None:
    """Create command stubs that model a mounted active 0.1.29 service."""
    commands = {
        "mountpoint": "#!/bin/sh\nexit 0\n",
        "findmnt": "#!/bin/sh\nprintf '/dev/tasca-data\\n'\n",
        "systemctl": "#!/bin/sh\ncase \"$1\" in\n  is-enabled) printf 'enabled\\n' ;;\n  is-active) printf 'active\\n' ;;\n  *) exit 0 ;;\nesac\n",
        "curl": "#!/bin/sh\ncase \"$*\" in\n  *'/api/v1/health'*) printf '{\"version\":\"0.1.29\"}' ;;\n  *'/api/v1/tables'*)\n    count_file=\"${TASCA_FAKE_TABLE_READ_COUNT_FILE:-}\"\n    if [ -n \"$count_file\" ]; then\n      count=0; [ -f \"$count_file\" ] && count=$(cat \"$count_file\")\n      count=$((count + 1)); printf '%s' \"$count\" > \"$count_file\"\n      if [ \"${TASCA_MUTATE_DB_ON_TABLE_READ:-}\" = \"$count\" ]; then printf x >> \"$TASCA_TEST_DB_FILE\"; fi\n    fi\n    printf '[]' ;;\n  *) printf '[]' ;;\nesac\n",
        "realpath": "#!/usr/bin/env bash\npath=''\nfor argument in \"$@\"; do path=\"$argument\"; done\n[[ -e \"$path\" ]] || exit 1\npython3 - \"$path\" <<'PY'\nfrom pathlib import Path\nimport sys\nprint(Path(sys.argv[1]).resolve())\nPY\n",
        "stat": "#!/usr/bin/env bash\n[[ \"$1\" == '-c' ]] || exit 2\nformat=\"$2\"\npath=''\nfor argument in \"$@\"; do path=\"$argument\"; done\npython3 - \"$format\" \"$path\" <<'PY'\nimport os\nimport sys\nfmt, path = sys.argv[1:]\nst = os.stat(path)\nvalues = {'%a': format(st.st_mode & 0o777, 'o'), '%d:%i:%s': f'{st.st_dev}:{st.st_ino}:{st.st_size}'}\nprint(values[fmt])\nPY\n",
        "cp": "#!/usr/bin/env bash\nargs=(\"$@\")\ncount=${#args[@]}\nsource=\"${args[$((count - 2))]}\"\ntarget=\"${args[$((count - 1))]}\"\n/bin/cp \"$source\" \"$target\"\nchmod \"$(python3 - \"$source\" <<'PY'\nimport os, sys\nprint(format(os.stat(sys.argv[1]).st_mode & 0o777, 'o'))\nPY\n)\" \"$target\"\n",
    }
    for name, content in commands.items():
        path = directory / name
        path.write_text(content)
        path.chmod(0o755)


def fixture_environment(tmp_path: Path, db_line: str = "TASCA_DB_PATH=/var/lib/tasca/tasca.db") -> tuple[dict[str, str], Path, Path, Path]:
    """Create isolated systemd/env/database fixture files with no production DB."""
    root = tmp_path / "fixture-root"
    data = root / "var/lib/tasca"
    env_file = root / "etc/tasca/tasca.env"
    unit_file = root / "etc/systemd/system/tasca.service"
    for path in (data, env_file.parent, unit_file.parent, root / "run"):
        path.mkdir(parents=True, exist_ok=True)
    database = data / "tasca.db"
    database.write_bytes(b"fixture sqlite bytes")
    env_file.write_text(f"TASCA_ENVIRONMENT=production\n{db_line}\nTASCA_API_HOST=0.0.0.0\n")
    env_file.chmod(0o640)
    unit_file.write_text("[Service]\nExecStart=/prior/release/tasca\n")
    unit_file.chmod(0o644)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_commands(bin_dir)
    environment = os.environ.copy()
    environment.update(
        {
            "TASCA_ROLLOUT_TEST_ROOT": str(root),
            "TASCA_ROLLOUT_TESTING": "1",
            "PATH": f"{bin_dir}:{environment['PATH']}",
        }
    )
    return environment, root, env_file, unit_file


def remote(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one remote-producer action against the isolated fixture."""
    return subprocess.run(
        ["bash", str(REMOTE), *arguments],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("db_line", "expected"),
    [
        ("", "exactly once"),
        ("TASCA_DB_PATH=relative.db", "absolute"),
        ("TASCA_DB_PATH=/outside/tasca.db", "on tasca-data"),
        ("TASCA_DB_PATH=/var/lib/tasca/missing.db", "must exist"),
    ],
)
def test_preflight_rejects_invalid_database_placement(
    tmp_path: Path, db_line: str, expected: str
) -> None:
    """Missing, relative, off-disk, and nonexistent DB paths stop before backup."""
    environment, root, env_file, _unit_file = fixture_environment(tmp_path, db_line or "TASCA_ENVIRONMENT=production")
    if not db_line:
        env_file.write_text("TASCA_ENVIRONMENT=production\n")
    if "outside" in db_line:
        outside = root / "outside"
        outside.mkdir()
        (outside / "tasca.db").write_bytes(b"outside fixture")
        env_file.write_text("TASCA_ENVIRONMENT=production\nTASCA_DB_PATH=/outside/tasca.db\n")

    result = remote(environment, "preflight", "--rollback-version", "0.1.29")

    assert result.returncode != 0
    assert expected in result.stderr
    assert not (root / "var/lib/tasca/rollback/0.1.29").exists()


def test_preflight_rejects_duplicate_database_path(tmp_path: Path) -> None:
    """Duplicate environment entries cannot ambiguously select a database."""
    environment, root, env_file, _unit_file = fixture_environment(tmp_path)
    env_file.write_text(
        "TASCA_DB_PATH=/var/lib/tasca/tasca.db\nTASCA_DB_PATH=/var/lib/tasca/tasca.db\n"
    )

    result = remote(environment, "preflight", "--rollback-version", "0.1.29")

    assert result.returncode != 0
    assert "exactly once" in result.stderr
    assert not (root / "var/lib/tasca/rollback/0.1.29").exists()


def test_preflight_rejects_nonpublic_rollback_environment(tmp_path: Path) -> None:
    """A rollback baseline with a Viewer token cannot promise public 0.1.29 reads."""
    environment, root, env_file, _unit_file = fixture_environment(tmp_path)
    env_file.write_text("TASCA_DB_PATH=/var/lib/tasca/tasca.db\nTASCA_VIEWER_TOKEN=prior-token\n")

    result = remote(environment, "preflight", "--rollback-version", "0.1.29")

    assert result.returncode != 0
    assert "Viewer reads public" in result.stderr
    assert not (root / "var/lib/tasca/rollback/0.1.29").exists()


def test_preflight_captures_database_identity_after_initialization_read(tmp_path: Path) -> None:
    """The first old-runtime read settles DB bytes before the rollback hash is recorded."""
    environment, root, _env_file, _unit_file = fixture_environment(tmp_path)
    database = root / "var/lib/tasca/tasca.db"
    count_file = tmp_path / "table-read-count"
    environment.update(
        {
            "TASCA_TEST_DB_FILE": str(database),
            "TASCA_FAKE_TABLE_READ_COUNT_FILE": str(count_file),
            "TASCA_MUTATE_DB_ON_TABLE_READ": "1",
        }
    )

    result = remote(environment, "preflight", "--rollback-version", "0.1.29")

    assert result.returncode == 0, result.stderr
    assert count_file.read_text() == "1"
    recorded_hash = (root / "var/lib/tasca/rollback/0.1.29/database.sha256").read_text().strip()
    assert recorded_hash == hashlib.sha256(database.read_bytes()).hexdigest()


def test_rollback_rejects_database_mutated_by_final_public_read(tmp_path: Path) -> None:
    """Identity is asserted after the rollback read, so late DB writes cannot pass rollback."""
    environment, root, _env_file, _unit_file = fixture_environment(tmp_path)
    database = root / "var/lib/tasca/tasca.db"
    count_file = tmp_path / "table-read-count"
    environment.update(
        {
            "TASCA_TEST_DB_FILE": str(database),
            "TASCA_FAKE_TABLE_READ_COUNT_FILE": str(count_file),
            "TASCA_MUTATE_DB_ON_TABLE_READ": "2",
        }
    )
    assert remote(environment, "preflight", "--rollback-version", "0.1.29").returncode == 0

    result = remote(
        environment,
        "rollback",
        "--rollback-version",
        "0.1.29",
        "--https-host",
        "tasca.example.test",
    )

    assert result.returncode != 0
    assert "database device, inode, or size changed" in result.stderr
    assert count_file.read_text() == "2"


def test_rollback_restores_exact_unit_env_modes_and_database_identity(tmp_path: Path) -> None:
    """Fixture rollback restores captured bytes/modes and leaves the fixture DB unchanged."""
    environment, root, env_file, unit_file = fixture_environment(tmp_path)
    database = root / "var/lib/tasca/tasca.db"
    before_database = (database.stat().st_dev, database.stat().st_ino, database.read_bytes())
    original_env = env_file.read_bytes()
    original_unit = unit_file.read_bytes()
    original_env_mode = env_file.stat().st_mode & 0o777
    original_unit_mode = unit_file.stat().st_mode & 0o777

    preflight = remote(environment, "preflight", "--rollback-version", "0.1.29")
    assert preflight.returncode == 0, preflight.stderr
    env_file.write_text("TASCA_DB_PATH=/var/lib/tasca/tasca.db\nTASCA_VIEWER_TOKEN=clear\n")
    env_file.chmod(0o600)
    unit_file.write_text("[Service]\nExecStart=/new/release/tasca\n")
    unit_file.chmod(0o600)

    rollback = remote(
        environment,
        "rollback",
        "--rollback-version",
        "0.1.29",
        "--https-host",
        "tasca.example.test",
    )

    assert rollback.returncode == 0, rollback.stderr
    assert env_file.read_bytes() == original_env
    assert unit_file.read_bytes() == original_unit
    assert (env_file.stat().st_mode & 0o777) == original_env_mode
    assert (unit_file.stat().st_mode & 0o777) == original_unit_mode
    assert (database.stat().st_dev, database.stat().st_ino, database.read_bytes()) == before_database
