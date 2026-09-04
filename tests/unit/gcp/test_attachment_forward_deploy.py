"""Offline contracts for the narrow 0.1.32 attachment forward deployment."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).parents[3]
DEPLOY = REPOSITORY / "scripts/gcp/attachment-forward-deploy.sh"
VERSION = "0.1.32"
PREVIOUS_VERSION = "0.1.31"
WHEEL_NAME = f"tasca-{VERSION}-py3-none-any.whl"


def write_fake_commands(directory: Path) -> None:
    """Install process seams that model only the permitted activation effects."""
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
            "case \"$1\" in is-active) printf 'active\\n' ;; *) exit 0 ;; esac\n"
        ),
        "curl": (
            "#!/bin/sh\n"
            "printf 'curl %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n"
            "case \"$*\" in *'/api/v1/health'*) printf '{\\\"version\\\":\\\"%s\\\"}' \"${TASCA_FAKE_VERSION}\" ;; *) exit 2 ;; esac\n"
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
        "python3": "#!/bin/sh\nprintf 'python3 %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\nexec \"$TASCA_REAL_TEST_PYTHON\" \"$@\"\n",
        "gcloud": (
            "#!/bin/sh\n"
            "printf 'gcloud %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n"
            "if [ \"$1 $2\" = 'compute ssh' ]; then exit \"${TASCA_GCLOUD_SSH_STATUS:-0}\"; fi\n"
            "exit 0\n"
        ),
        "uv": (
            "#!/usr/bin/env bash\n"
            "printf 'uv %s\\n' \"$*\" >> \"$TASCA_UV_LOG\"\n"
            "if [[ \"$1\" == venv ]]; then\n"
            "  target=\"${!#}\"; mkdir -p \"$target/bin\"\n"
            "  cat > \"$target/bin/python\" <<'PYTHON'\n"
            "#!/usr/bin/env bash\n"
            "if [[ \"${1:-}\" == -c && \"${2:-}\" == *importlib.metadata* ]]; then exit 0; fi\n"
            "exec \"$TASCA_FORWARD_TEST_PYTHON\" \"$@\"\n"
            "PYTHON\n"
            "  printf '#!/bin/sh\\nexit 0\\n' > \"$target/bin/tasca\"\n"
            "  chmod 0700 \"$target/bin/python\" \"$target/bin/tasca\"\n"
            "fi\n"
        ),
    }
    for name, content in commands.items():
        path = directory / name
        path.write_text(content)
        path.chmod(0o755)


def fixture_environment(
    tmp_path: Path, *, health_version: str
) -> tuple[dict[str, str], Path, Path, Path, Path, Path]:
    """Create a temporary remote root with protected state snapshots and logs."""
    root = tmp_path / "fixture-root"
    unit = root / "etc/systemd/system/tasca.service"
    environment = root / "etc/tasca/tasca.env"
    database = root / "var/lib/tasca/tasca.db"
    caddy = root / "etc/caddy/Caddyfile"
    previous_tasca = root / f"opt/tasca/releases/{PREVIOUS_VERSION}/venv/bin/tasca"
    for path in (unit.parent, environment.parent, database.parent, caddy.parent, previous_tasca.parent):
        path.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Service]\nUser=tasca\n"
        f"ExecStart={previous_tasca}\nRestart=on-failure\n"
    )
    unit.chmod(0o644)
    environment.write_text("TASCA_DB_PATH=/var/lib/tasca/tasca.db\nTASCA_ADMIN_TOKEN=fixture-kept-secret\n")
    database.write_bytes(b"fixture SQLite bytes must stay unchanged")
    caddy.write_text("existing.example { reverse_proxy 127.0.0.1:8000 }\n")
    previous_tasca.write_text("#!/bin/sh\nexit 0\n")
    previous_tasca.chmod(0o700)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_commands(bin_dir)
    selected_python = tmp_path / "selected-python"
    selected_python.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'selected-python %s\\n' \"$*\" >> \"$TASCA_SELECTED_PYTHON_LOG\"\n"
        "exec \"$TASCA_REAL_TEST_PYTHON\" \"$@\"\n"
    )
    selected_python.chmod(0o755)
    selected_python_log = tmp_path / "selected-python.log"
    selected_python_log.write_text("")
    wheel = tmp_path / WHEEL_NAME
    wheel.write_bytes(b"exact 0.1.32 fixture wheel")
    command_log = tmp_path / "commands.log"
    uv_log = tmp_path / "uv.log"
    command_log.write_text("")
    uv_log.write_text("")
    values = os.environ.copy()
    values.update(
        {
            "TASCA_FORWARD_TEST_ROOT": str(root),
            "TASCA_FORWARD_TESTING": "1",
            "TASCA_FORWARD_TEST_PYTHON": str(selected_python),
            "TASCA_REAL_TEST_PYTHON": sys.executable,
            "TASCA_SELECTED_PYTHON_LOG": str(selected_python_log),
            "TASCA_COMMAND_LOG": str(command_log),
            "TASCA_UV_LOG": str(uv_log),
            "TASCA_FAKE_VERSION": health_version,
            "PATH": f"{bin_dir}:{values['PATH']}",
            "RELEASE_WHEEL": str(wheel),
            "RELEASE_SHA256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        }
    )
    return values, root, unit, environment, database, caddy


def deploy(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one tracked producer action only against the temporary fixture root."""
    return subprocess.run(
        ["bash", str(DEPLOY), *arguments],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_preflight_is_read_only_and_binds_the_exact_current_release(tmp_path: Path) -> None:
    """Preflight verifies 0.1.31 health and absence of 0.1.32 without creating a venv."""
    environment, root, unit, env_file, database, caddy = fixture_environment(
        tmp_path, health_version=PREVIOUS_VERSION
    )
    snapshots = {path: path.read_bytes() for path in (unit, env_file, database, caddy)}

    result = deploy(environment, "preflight")

    assert result.returncode == 0, result.stderr
    assert "active 0.1.31" in result.stdout
    assert not (root / f"opt/tasca/releases/{VERSION}").exists()
    assert {path: path.read_bytes() for path in snapshots} == snapshots
    assert Path(environment["TASCA_UV_LOG"]).read_text() == ""
    assert "systemctl restart" not in Path(environment["TASCA_COMMAND_LOG"]).read_text()


def test_activate_installs_exact_wheel_and_preserves_forbidden_surfaces(tmp_path: Path) -> None:
    """A healthy activation changes only ExecStart and creates the fresh 0.1.32 venv."""
    environment, root, unit, env_file, database, caddy = fixture_environment(
        tmp_path, health_version=VERSION
    )
    original_unit = unit.read_bytes()
    snapshots = {path: path.read_bytes() for path in (env_file, database, caddy)}

    result = deploy(
        environment,
        "activate",
        "--wheel",
        environment["RELEASE_WHEEL"],
        "--wheel-sha",
        environment["RELEASE_SHA256"],
    )

    assert result.returncode == 0, result.stderr
    assert f"ExecStart={root}/opt/tasca/releases/{VERSION}/venv/bin/tasca" in unit.read_text()
    backup = root / f"opt/tasca/releases/{VERSION}/tasca.service.pre-{VERSION}"
    assert backup.read_bytes() == original_unit
    assert {path: path.read_bytes() for path in snapshots} == snapshots
    assert (root / f"opt/tasca/releases/{PREVIOUS_VERSION}/venv/bin/tasca").is_file()
    assert (root / f"opt/tasca/releases/{VERSION}/venv/bin/tasca").is_file()
    command_log = Path(environment["TASCA_COMMAND_LOG"]).read_text()
    assert command_log.count("systemctl restart tasca.service") == 1
    assert "http://127.0.0.1:8000/api/v1/health" in command_log
    assert "https://34.1.134.239.sslip.io/api/v1/health" in command_log
    assert "fixture-kept-secret" not in result.stdout + result.stderr + command_log
    assert "python3 " not in command_log
    assert "json.load" in Path(environment["TASCA_SELECTED_PYTHON_LOG"]).read_text()


def test_activation_failure_restores_the_saved_unit_and_restarts_0_1_31(tmp_path: Path) -> None:
    """A bounded 0.1.32 health failure restores the exact 0.1.31 service selection."""
    environment, _root, unit, env_file, database, _caddy = fixture_environment(
        tmp_path, health_version=PREVIOUS_VERSION
    )
    original_unit = unit.read_bytes()
    snapshots = {path: path.read_bytes() for path in (env_file, database)}

    result = deploy(
        environment,
        "activate",
        "--wheel",
        environment["RELEASE_WHEEL"],
        "--wheel-sha",
        environment["RELEASE_SHA256"],
    )

    assert result.returncode != 0
    assert "pre-0.1.32 unit restored and 0.1.31 restarted" in result.stderr
    assert unit.read_bytes() == original_unit
    assert {path: path.read_bytes() for path in snapshots} == snapshots
    command_log = Path(environment["TASCA_COMMAND_LOG"]).read_text()
    assert command_log.count("systemctl restart tasca.service") == 2


def test_bad_wheel_digest_fails_before_service_or_package_mutation(tmp_path: Path) -> None:
    """A wrong caller-bound SHA cannot reach venv creation or the systemd unit change."""
    environment, root, unit, _env_file, _database, _caddy = fixture_environment(
        tmp_path, health_version=VERSION
    )
    original_unit = unit.read_bytes()

    result = deploy(
        environment,
        "activate",
        "--wheel",
        environment["RELEASE_WHEEL"],
        "--wheel-sha",
        "0" * 64,
    )

    assert result.returncode != 0
    assert "RELEASE_SHA256 does not match RELEASE_WHEEL" in result.stderr
    assert unit.read_bytes() == original_unit
    assert not (root / f"opt/tasca/releases/{VERSION}").exists()
    assert Path(environment["TASCA_COMMAND_LOG"]).read_text() == ""
    assert Path(environment["TASCA_UV_LOG"]).read_text() == ""


def test_apply_stops_at_remote_read_only_preflight_before_stage_or_scp(tmp_path: Path) -> None:
    """A first remote reconciliation failure reaches no stage directory or SCP path."""
    environment, _root, _unit, _env_file, _database, _caddy = fixture_environment(
        tmp_path, health_version=PREVIOUS_VERSION
    )
    environment["TASCA_GCLOUD_SSH_STATUS"] = "42"

    result = deploy(environment, "apply")

    assert result.returncode != 0
    command_log = Path(environment["TASCA_COMMAND_LOG"]).read_text()
    assert command_log.startswith("gcloud compute ssh tasca-mcp ")
    assert "gcloud compute scp" not in command_log
    assert "/var/tmp/tasca-attachments-0.1.32" not in command_log
    assert "install -d" not in command_log


def test_render_binds_target_current_release_wheel_and_new_verifier(tmp_path: Path) -> None:
    """The effect-free manifest states the immutable inputs required by later runtime work."""
    environment, _root, _unit, _env_file, _database, _caddy = fixture_environment(
        tmp_path, health_version=PREVIOUS_VERSION
    )

    result = deploy(environment, "render")

    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["release"] == {
        "version": VERSION,
        "wheel": WHEEL_NAME,
        "sha256": environment["RELEASE_SHA256"],
    }
    assert manifest["previous_release"] == PREVIOUS_VERSION
    assert manifest["target"] == {
        "project": "rda-engineering",
        "zone": "asia-southeast1-b",
        "vm": "tasca-mcp",
        "https_host": "34.1.134.239.sslip.io",
    }
    assert manifest["verifier"]["path"] == "scripts/gcp/verify_attachments_remote.py"
    assert manifest["verifier"]["expected_version"] == VERSION
    assert manifest["producer"]["revision"] == manifest["verifier"]["revision"]
    assert "remote_read_only_preflight" in manifest["actions"]
    assert manifest["actions"][-1].endswith("restart_0_1_31_on_activation_failure")
