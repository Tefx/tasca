"""Offline contracts for the narrow 0.1.31 Seat-presence forward deployment."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).parents[3]
DEPLOY = REPOSITORY / "scripts/gcp/seat-presence-forward-deploy.sh"
VERSION = "0.1.31"
PREVIOUS_VERSION = "0.1.30"
WHEEL_NAME = f"tasca-{VERSION}-py3-none-any.whl"


def write_fake_commands(directory: Path) -> None:
    """Install fixture seams for process effects without a VM or package install."""
    commands = {
        "install": (
            "#!/usr/bin/env bash\n"
            "printf 'install %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n"
            "args=(\"$@\")\n"
            "last=\"${args[$(( ${#args[@]} - 1 ))]}\"\n"
            "case \" $* \" in *' -d '*) mkdir -p \"$last\"; exit 0 ;; esac\n"
            "exit 2\n"
        ),
        "chown": "#!/bin/sh\nprintf 'chown %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n",
        "cp": (
            "#!/usr/bin/env bash\n"
            "args=(\"$@\")\n"
            "count=${#args[@]}\n"
            "/bin/cp \"${args[$((count - 2))]}\" \"${args[$((count - 1))]}\"\n"
        ),
        "systemctl": (
            "#!/bin/sh\n"
            "printf 'systemctl %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n"
            "case \"$1\" in\n"
            "  is-active) printf 'active\\n' ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        ),
        "curl": (
            "#!/bin/sh\n"
            "printf 'curl %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n"
            "case \"$*\" in\n"
            "  *'/api/v1/health'*) printf '{\\\"version\\\":\\\"%s\\\"}' \"${TASCA_FAKE_VERSION}\" ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n"
        ),
        "realpath": (
            "#!/usr/bin/env bash\n"
            "path=\"${!#}\"\n"
            "[[ -e \"$path\" ]] || exit 1\n"
            "\"$TASCA_FORWARD_TEST_PYTHON\" - \"$path\" <<'PYTHON'\n"
            "from pathlib import Path\n"
            "import sys\n"
            "print(Path(sys.argv[1]).resolve())\n"
            "PYTHON\n"
        ),
        "uv": (
            "#!/usr/bin/env bash\n"
            "printf 'uv %s\\n' \"$*\" >> \"$TASCA_UV_LOG\"\n"
            "if [[ \"$1\" == venv ]]; then\n"
            "  target=\"${!#}\"\n"
            "  mkdir -p \"$target/bin\"\n"
            "  cat > \"$target/bin/python\" <<'PYTHON'\n"
            "#!/usr/bin/env bash\n"
            "if [[ \"${1:-}\" == -c && \"${2:-}\" == *importlib.metadata* ]]; then exit 0; fi\n"
            "exec \"$TASCA_FORWARD_TEST_PYTHON\" \"$@\"\n"
            "PYTHON\n"
            "  cat > \"$target/bin/tasca\" <<'TASCA'\n"
            "#!/bin/sh\n"
            "exit 0\n"
            "TASCA\n"
            "  chmod 0700 \"$target/bin/python\" \"$target/bin/tasca\"\n"
            "fi\n"
        ),
    }
    for name, content in commands.items():
        path = directory / name
        path.write_text(content)
        path.chmod(0o755)


def fixture_environment(tmp_path: Path, *, health_version: str = VERSION) -> tuple[dict[str, str], Path, Path, Path, Path]:
    """Create an isolated current release, preserved surfaces, and effect logs."""
    root = tmp_path / "fixture-root"
    unit = root / "etc/systemd/system/tasca.service"
    environment = root / "etc/tasca/tasca.env"
    database = root / "var/lib/tasca/tasca.db"
    caddy = root / "etc/caddy/Caddyfile"
    previous_tasca = root / f"opt/tasca/releases/{PREVIOUS_VERSION}/venv/bin/tasca"
    for path in (unit.parent, environment.parent, database.parent, caddy.parent, previous_tasca.parent):
        path.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Service]\n"
        "User=tasca\n"
        f"ExecStart={previous_tasca}\n"
        "Restart=on-failure\n"
    )
    unit.chmod(0o644)
    environment.write_text("TASCA_DB_PATH=/var/lib/tasca/tasca.db\nTASCA_ADMIN_TOKEN=fixture-kept-secret\n")
    environment.chmod(0o640)
    database.write_bytes(b"fixture SQLite bytes must stay unchanged")
    caddy.write_text("existing.example { reverse_proxy 127.0.0.1:8000 }\n")
    previous_tasca.write_text("#!/bin/sh\nexit 0\n")
    previous_tasca.chmod(0o700)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_commands(bin_dir)
    command_log = tmp_path / "commands.log"
    uv_log = tmp_path / "uv.log"
    command_log.write_text("")
    uv_log.write_text("")
    release_wheel = tmp_path / WHEEL_NAME
    release_wheel.write_bytes(b"exact 0.1.31 fixture wheel")
    values = os.environ.copy()
    values.update(
        {
            "TASCA_FORWARD_TEST_ROOT": str(root),
            "TASCA_FORWARD_TESTING": "1",
            "TASCA_FORWARD_TEST_PYTHON": sys.executable,
            "TASCA_COMMAND_LOG": str(command_log),
            "TASCA_UV_LOG": str(uv_log),
            "TASCA_FAKE_VERSION": health_version,
            "TASCA_ADMIN_TOKEN": "must-not-appear-in-output",
            "PATH": f"{bin_dir}:{values['PATH']}",
        }
    )
    values["RELEASE_WHEEL"] = str(release_wheel)
    values["RELEASE_SHA256"] = hashlib.sha256(release_wheel.read_bytes()).hexdigest()
    return values, root, unit, environment, database


def activate(environment: dict[str, str], *, digest: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run only the remote activation path against the isolated fixture root."""
    return subprocess.run(
        [
            "bash",
            str(DEPLOY),
            "activate",
            "--wheel",
            environment["RELEASE_WHEEL"],
            "--wheel-sha",
            digest or environment["RELEASE_SHA256"],
        ],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_activate_installs_exact_wheel_and_only_switches_the_unit_execstart(tmp_path: Path) -> None:
    """A healthy activation retains protected state and atomically selects the 0.1.31 venv."""
    environment, root, unit, env_file, database = fixture_environment(tmp_path)
    original_unit = unit.read_bytes()
    original_env = env_file.read_bytes()
    original_database = database.read_bytes()
    caddy = root / "etc/caddy/Caddyfile"
    original_caddy = caddy.read_bytes()

    result = activate(environment)

    assert result.returncode == 0, result.stderr
    current_exec = f"ExecStart={root}/opt/tasca/releases/{VERSION}/venv/bin/tasca"
    assert unit.read_text() == original_unit.decode().replace(
        f"ExecStart={root}/opt/tasca/releases/{PREVIOUS_VERSION}/venv/bin/tasca", current_exec
    )
    backup = root / f"opt/tasca/releases/{VERSION}/tasca.service.pre-{VERSION}"
    assert backup.read_bytes() == original_unit
    assert (root / f"opt/tasca/releases/{PREVIOUS_VERSION}/venv/bin/tasca").is_file()
    assert (root / f"opt/tasca/releases/{VERSION}/venv/bin/tasca").is_file()
    assert env_file.read_bytes() == original_env
    assert database.read_bytes() == original_database
    assert caddy.read_bytes() == original_caddy

    command_log = Path(environment["TASCA_COMMAND_LOG"]).read_text()
    assert "systemctl daemon-reload" in command_log
    assert command_log.count("systemctl restart tasca.service") == 1
    assert "http://127.0.0.1:8000/api/v1/health" in command_log
    assert "https://34.1.134.239.sslip.io/api/v1/health" in command_log
    assert "must-not-appear-in-output" not in result.stdout + result.stderr + command_log
    uv_log = Path(environment["TASCA_UV_LOG"]).read_text()
    assert f"venv --clear --python {sys.executable} {root}/opt/tasca/releases/{VERSION}/venv" in uv_log
    assert f"pip install --python {root}/opt/tasca/releases/{VERSION}/venv/bin/python" in uv_log


def test_activation_failure_restores_the_original_unit_and_preserves_state(tmp_path: Path) -> None:
    """Bounded health failure restores the pre-release unit before the producer reports failure."""
    environment, root, unit, env_file, database = fixture_environment(tmp_path, health_version=PREVIOUS_VERSION)
    original_unit = unit.read_bytes()
    original_env = env_file.read_bytes()
    original_database = database.read_bytes()

    result = activate(environment)

    assert result.returncode != 0
    assert "pre-0.1.31 unit restored" in result.stderr
    assert unit.read_bytes() == original_unit
    assert env_file.read_bytes() == original_env
    assert database.read_bytes() == original_database
    assert (root / f"opt/tasca/releases/{PREVIOUS_VERSION}/venv/bin/tasca").is_file()
    command_log = Path(environment["TASCA_COMMAND_LOG"]).read_text()
    assert command_log.count("systemctl restart tasca.service") == 2
    assert command_log.count("http://127.0.0.1:8000/api/v1/health") == 10


def test_activation_rejects_a_mismatched_wheel_before_any_service_change(tmp_path: Path) -> None:
    """A changed wheel digest cannot reach venv creation or the systemd unit switch."""
    environment, _root, unit, _env_file, _database = fixture_environment(tmp_path)
    original_unit = unit.read_bytes()

    result = activate(environment, digest="0" * 64)

    assert result.returncode != 0
    assert "RELEASE_SHA256 does not match RELEASE_WHEEL" in result.stderr
    assert unit.read_bytes() == original_unit
    assert Path(environment["TASCA_COMMAND_LOG"]).read_text() == ""
    assert Path(environment["TASCA_UV_LOG"]).read_text() == ""


def test_render_binds_the_exact_target_wheel_python_and_current_verifier(tmp_path: Path) -> None:
    """The effect-free plan exposes only the admitted target and exact 0.1.31 inputs."""
    environment, _root, _unit, _env_file, _database = fixture_environment(tmp_path)

    result = subprocess.run(
        ["bash", str(DEPLOY), "render"],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["release"] == {
        "version": VERSION,
        "wheel": WHEEL_NAME,
        "sha256": environment["RELEASE_SHA256"],
    }
    assert manifest["target"] == {
        "project": "rda-engineering",
        "zone": "asia-southeast1-b",
        "vm": "tasca-mcp",
        "https_host": "34.1.134.239.sslip.io",
    }
    assert manifest["python"]["required"] == "CPython 3.13"
    assert manifest["verifier"]["expected_version"] == VERSION
    assert manifest["actions"][-2:] == [
        "bounded_local_and_https_health_readback",
        "restore_pre_0_1_31_unit_on_activation_failure",
    ]
