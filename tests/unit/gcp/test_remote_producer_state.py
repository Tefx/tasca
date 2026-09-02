"""Offline behavioral tests for remote rollout state, health, and rollback inputs."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parents[3]
REMOTE = REPOSITORY / "scripts/gcp/viewer-auth-remote.sh"
BUNDLE_BUILDER = REPOSITORY / "scripts/gcp/build-viewer-auth-rollback-bundle.sh"


def write_wheel(path: Path, *, name: str, version: str, requires: tuple[str, ...] = ()) -> None:
    """Create a metadata-only wheel used solely to exercise offline bundle handling."""
    metadata = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    if name == "tasca":
        metadata.append("Requires-Python: >=3.13")
    metadata.extend(f"Requires-Dist: {item}" for item in requires)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA", "\n".join(metadata) + "\n")


def rollback_bundle(tmp_path: Path) -> tuple[Path, str]:
    """Build a real immutable rollback archive used by remote fixture commands."""
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    tasca = wheelhouse / "tasca-0.1.29-py3-none-any.whl"
    httpx = wheelhouse / "httpx-0.28.1-py3-none-any.whl"
    fastmcp = wheelhouse / "fastmcp-3.1.0-py3-none-any.whl"
    write_wheel(tasca, name="tasca", version="0.1.29", requires=("httpx (>=0.28.0)", "fastmcp (>=3.0)"))
    write_wheel(httpx, name="httpx", version="0.28.1")
    write_wheel(fastmcp, name="fastmcp", version="3.1.0")
    bundle = tmp_path / "rollback.tar.gz"
    receipt = tmp_path / "rollback-receipt.json"
    build = subprocess.run(
        [
            "bash",
            str(BUNDLE_BUILDER),
            "build",
            "--wheel",
            str(tasca),
            "--wheel-sha256",
            hashlib.sha256(tasca.read_bytes()).hexdigest(),
            "--wheelhouse",
            str(wheelhouse),
            "--output",
            str(bundle),
            "--receipt",
            str(receipt),
        ],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    return bundle, hashlib.sha256(bundle.read_bytes()).hexdigest()


def write_fake_commands(directory: Path) -> None:
    """Create process seams that model an old or new healthy local Tasca service."""
    commands = {
        "mountpoint": "#!/bin/sh\nexit 0\n",
        "findmnt": "#!/bin/sh\nprintf '/dev/tasca-data\\n'\n",
        "systemctl": (
            "#!/bin/sh\n"
            "printf 'systemctl %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n"
            "case \"$1\" in\n"
            "  is-enabled) printf 'enabled\\n' ;;\n"
            "  is-active) printf 'active\\n' ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        ),
        "curl": (
            "#!/bin/sh\n"
            "printf 'curl %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n"
            "case \"$*\" in\n"
            "  *ssl_verify_result*) printf '%s' \"${TASCA_FAKE_TLS_VERIFY:-0}\" ;;\n"
            "  *metadata.google.internal*project-id*) printf 'rda-engineering' ;;\n"
            "  *metadata.google.internal*service-accounts*) printf '{\\\"access_token\\\":\\\"fixture-access\\\"}' ;;\n"
            "  *secretmanager.googleapis.com*tasca-admin-token*) printf '{\\\"payload\\\":{\\\"data\\\":\\\"YWRtaW4tZml4dHVyZQ==\\\"}}' ;;\n"
            "  *secretmanager.googleapis.com*tasca-viewer-token*) printf '{\\\"payload\\\":{\\\"data\\\":\\\"dmlld2VyLWZpeHR1cmU=\\\"}}' ;;\n"
            "  *'/api/v1/health'*)\n"
            "    case \"$*\" in\n"
            "      *127.0.0.1*) counter=\"$TASCA_FAKE_LOCAL_HEALTH_COUNTER\"; failures=\"${TASCA_FAKE_LOCAL_HEALTH_FAILURES:-${TASCA_FAKE_HEALTH_FAILURES:-0}}\" ;;\n"
            "      *) counter=\"$TASCA_FAKE_HTTPS_HEALTH_COUNTER\"; failures=\"${TASCA_FAKE_HTTPS_HEALTH_FAILURES:-${TASCA_FAKE_HEALTH_FAILURES:-0}}\" ;;\n"
            "    esac\n"
            "    seen=$(cat \"$counter\")\n"
            "    if [ \"$seen\" -lt \"$failures\" ]; then\n"
            "      printf '%s\\n' $((seen + 1)) > \"$counter\"\n"
            "      exit 7\n"
            "    fi\n"
            "    if [ \"${TASCA_FAKE_OMIT_VIEWER:-0}\" = 1 ]; then\n"
            "      printf '{\\\"version\\\":\\\"%s\\\"}' \"${TASCA_FAKE_VERSION:-0.1.29}\"\n"
            "    else\n"
            "      printf '{\\\"version\\\":\\\"%s\\\",\\\"viewer_auth_required\\\":%s}' \"${TASCA_FAKE_VERSION:-0.1.29}\" \"${TASCA_FAKE_VIEWER:-false}\"\n"
            "    fi\n"
            "    ;;\n"
            "  *'/api/v1/tables'*)\n"
            "    if [ \"${TASCA_MUTATE_DB_ON_TABLE_READ:-0}\" = 1 ]; then printf x >> \"$TASCA_TEST_DB_FILE\"; fi\n"
            "    printf '[]' ;;\n"
            "  *) printf '[]' ;;\n"
            "esac\n"
        ),
        "realpath": (
            "#!/usr/bin/env bash\n"
            "path=''\nfor argument in \"$@\"; do path=\"$argument\"; done\n"
            "[[ -e \"$path\" ]] || exit 1\n"
            "python3 - \"$path\" <<'PY'\nfrom pathlib import Path\nimport sys\nprint(Path(sys.argv[1]).resolve())\nPY\n"
        ),
        "stat": (
            "#!/usr/bin/env bash\n"
            "[[ \"$1\" == '-c' ]] || exit 2\nformat=\"$2\"\npath=''\nfor argument in \"$@\"; do path=\"$argument\"; done\n"
            "python3 - \"$format\" \"$path\" <<'PY'\nimport os\nimport sys\nfmt, path = sys.argv[1:]\nst = os.stat(path)\nvalues = {'%a': format(st.st_mode & 0o777, 'o'), '%d:%i:%s': f'{st.st_dev}:{st.st_ino}:{st.st_size}'}\nprint(values[fmt])\nPY\n"
        ),
        "cp": (
            "#!/usr/bin/env bash\nargs=(\"$@\")\ncount=${#args[@]}\nsource=\"${args[$((count - 2))]}\"\ntarget=\"${args[$((count - 1))]}\"\n/bin/cp \"$source\" \"$target\"\n"
            "chmod \"$(python3 - \"$source\" <<'PY'\nimport os, sys\nprint(format(os.stat(sys.argv[1]).st_mode & 0o777, 'o'))\nPY\n)\" \"$target\"\n"
        ),
        "install": (
            "#!/usr/bin/env bash\n"
            "printf 'install %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n"
            "args=(\"$@\")\nlast=\"${args[$(( ${#args[@]} - 1 ))]}\"\n"
            "case \" $* \" in *' -d '*) mkdir -p \"$last\"; exit 0 ;; esac\n"
            "source=\"${args[$(( ${#args[@]} - 2 ))]}\"\nmkdir -p \"$(dirname \"$last\")\"\n/bin/cp \"$source\" \"$last\"\n"
            "case \" $* \" in *' -m 0640 '*) chmod 0640 \"$last\" ;; esac\n"
        ),
        "chown": "#!/bin/sh\nprintf 'chown %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n",
        "uv": (
            "#!/usr/bin/env bash\n"
            "printf 'uv %s\\n' \"$*\" >> \"$TASCA_UV_LOG\"\n"
            "if [[ \"$1\" == venv ]]; then\n target=\"${!#}\"; mkdir -p \"$target/bin\"; touch \"$target/bin/python\" \"$target/bin/tasca\"; chmod 0700 \"$target/bin/python\" \"$target/bin/tasca\"; fi\n"
        ),
        "caddy": "#!/bin/sh\nprintf 'caddy %s\\n' \"$*\" >> \"$TASCA_COMMAND_LOG\"\n",
    }
    for name, content in commands.items():
        path = directory / name
        path.write_text(content)
        path.chmod(0o755)


def fixture_environment(
    tmp_path: Path, db_line: str = "TASCA_DB_PATH=/var/lib/tasca/tasca.db"
) -> tuple[dict[str, str], Path, Path, Path, Path, str]:
    """Create isolated disk, unit, command seams, and exact selected Python input."""
    root = tmp_path / "fixture-root"
    data = root / "var/lib/tasca"
    env_file = root / "etc/tasca/tasca.env"
    unit_file = root / "etc/systemd/system/tasca.service"
    for path in (data, env_file.parent, unit_file.parent, root / "run"):
        path.mkdir(parents=True, exist_ok=True)
    database = data / "tasca.db"
    database.write_bytes(b"fixture sqlite bytes")
    env_file.write_text(f"TASCA_ENVIRONMENT=production\n{db_line}\n")
    env_file.chmod(0o640)
    unit_file.write_text("[Service]\nUser=tasca\nExecStart=uvx --from tasca==0.1.29 tasca\n")
    unit_file.chmod(0o644)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_commands(bin_dir)
    command_log = tmp_path / "commands.log"
    uv_log = tmp_path / "uv.log"
    local_health_counter = tmp_path / "local-health-counter"
    https_health_counter = tmp_path / "https-health-counter"
    command_log.write_text("")
    uv_log.write_text("")
    local_health_counter.write_text("0\n")
    https_health_counter.write_text("0\n")
    bundle, bundle_sha256 = rollback_bundle(tmp_path)
    release = tmp_path / "tasca-0.1.30-py3-none-any.whl"
    release.write_bytes(b"exact 0.1.30 fixture")
    environment = os.environ.copy()
    environment.update(
        {
            "TASCA_ROLLOUT_TEST_ROOT": str(root),
            "TASCA_ROLLOUT_TESTING": "1",
            "TASCA_COMMAND_LOG": str(command_log),
            "TASCA_UV_LOG": str(uv_log),
            "TASCA_FAKE_LOCAL_HEALTH_COUNTER": str(local_health_counter),
            "TASCA_FAKE_HTTPS_HEALTH_COUNTER": str(https_health_counter),
            "TASCA_TEST_DB_FILE": str(database),
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "TASCA_TEST_PYTHON": sys.executable,
            "TASCA_ROLLBACK_BUNDLE": str(bundle),
            "TASCA_ROLLBACK_SHA256": bundle_sha256,
            "TASCA_RELEASE_WHEEL": str(release),
            "TASCA_RELEASE_SHA256": hashlib.sha256(release.read_bytes()).hexdigest(),
        }
    )
    return environment, root, env_file, unit_file, command_log, bundle_sha256


def remote(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one remote producer action against only the isolated fixture root."""
    return subprocess.run(
        ["bash", str(REMOTE), *arguments],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def preflight_arguments(environment: dict[str, str]) -> list[str]:
    """Return the explicit interpreter and immutable input arguments required before effects."""
    return [
        "preflight",
        "--rollback-version",
        "0.1.29",
        "--python",
        environment["TASCA_TEST_PYTHON"],
        "--rollback-bundle",
        environment["TASCA_ROLLBACK_BUNDLE"],
        "--rollback-sha",
        environment["TASCA_ROLLBACK_SHA256"],
    ]


def release_arguments(environment: dict[str, str], action: str) -> list[str]:
    """Return complete exact release inputs for apply/reconcile/rehearse tests."""
    return [
        action,
        "--https-host",
        "tasca.example.test",
        "--wheel",
        environment["TASCA_RELEASE_WHEEL"],
        "--wheel-sha",
        environment["TASCA_RELEASE_SHA256"],
        "--release-version",
        "0.1.30",
        "--viewer-mode",
        "configured",
        "--python",
        environment["TASCA_TEST_PYTHON"],
        "--rollback-bundle",
        environment["TASCA_ROLLBACK_BUNDLE"],
        "--rollback-sha",
        environment["TASCA_ROLLBACK_SHA256"],
    ]


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
    """Rollback capture rejects missing, relative, and nonexistent database selections."""
    environment, root, _env_file, _unit_file, _command_log, _bundle_sha = fixture_environment(tmp_path, db_line)
    if "/outside/" in db_line:
        outside = root / "outside"
        outside.mkdir()
        (outside / "tasca.db").write_bytes(b"outside fixture")

    result = remote(environment, *preflight_arguments(environment))

    assert result.returncode != 0
    assert expected in result.stderr
    assert not (root / "var/lib/tasca/rollback/0.1.29").exists()


def test_preflight_rejects_duplicate_or_nonpublic_rollback_environment(tmp_path: Path) -> None:
    """One unambiguous public baseline is required before bytes can be captured."""
    environment, root, env_file, _unit_file, _command_log, _bundle_sha = fixture_environment(tmp_path)
    env_file.write_text(
        "TASCA_DB_PATH=/var/lib/tasca/tasca.db\n"
        "TASCA_DB_PATH=/var/lib/tasca/tasca.db\n"
    )
    duplicate = remote(environment, *preflight_arguments(environment))
    assert duplicate.returncode != 0
    assert "exactly once" in duplicate.stderr
    assert not (root / "var/lib/tasca/rollback/0.1.29").exists()

    env_file.write_text("TASCA_DB_PATH=/var/lib/tasca/tasca.db\nTASCA_VIEWER_TOKEN=fixture\n")
    configured = remote(environment, *preflight_arguments(environment))
    assert configured.returncode != 0
    assert "Viewer reads public" in configured.stderr


def test_preflight_captures_database_identity_after_initial_public_read(tmp_path: Path) -> None:
    """The baseline read settles database bytes before the rollback identity is recorded."""
    environment, root, _env_file, _unit_file, _command_log, _bundle_sha = fixture_environment(tmp_path)
    database = root / "var/lib/tasca/tasca.db"
    environment["TASCA_MUTATE_DB_ON_TABLE_READ"] = "1"

    result = remote(environment, *preflight_arguments(environment))

    assert result.returncode == 0, result.stderr
    captured = (root / "var/lib/tasca/rollback/0.1.29/database.sha256").read_text().strip()
    assert captured == hashlib.sha256(database.read_bytes()).hexdigest()


def test_preflight_rejects_incompatible_python_before_capture(tmp_path: Path) -> None:
    """The system-Python failure is rejected before rollback capture or any service work."""
    environment, root, _env_file, _unit_file, command_log, _bundle_sha = fixture_environment(tmp_path)
    incompatible = tmp_path / "python3.11"
    incompatible.write_text("#!/bin/sh\nprintf 'cpython:3.11'\n")
    incompatible.chmod(0o755)
    environment["TASCA_TEST_PYTHON"] = str(incompatible)

    result = remote(environment, *preflight_arguments(environment))

    assert result.returncode != 0
    assert "must select CPython 3.13 before rollout effects" in result.stderr
    assert not (root / "var/lib/tasca/rollback/0.1.29").exists()
    assert command_log.read_text() == ""


def test_tls_certificate_gate_survives_backend_down_but_activation_requires_real_local_and_https_health(tmp_path: Path) -> None:
    """TLS checks certificate validity without a backend, then apply requires both real health paths."""
    environment, _root, _env_file, _unit_file, command_log, _bundle_sha = fixture_environment(tmp_path)
    environment["TASCA_FAKE_BACKEND_DOWN"] = "1"
    tls = remote(environment, "stage-tls", "--https-host", "tasca.example.test")

    assert tls.returncode == 0, tls.stderr
    assert "certificate-valid HTTPS is active without backend health" in tls.stdout
    assert remote(environment, *preflight_arguments(environment)).returncode == 0
    stale = remote(environment, *release_arguments(environment, "apply"))

    assert stale.returncode != 0
    assert "local Tasca health is not the expected release" in stale.stderr
    environment.update({"TASCA_FAKE_VERSION": "0.1.30", "TASCA_FAKE_VIEWER": "true"})
    healthy = remote(environment, *release_arguments(environment, "apply"))

    assert healthy.returncode == 0, healthy.stderr
    logged = command_log.read_text()
    assert "curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health" in logged
    assert "curl --fail --silent --show-error --proto =https --tlsv1.2 https://tasca.example.test/api/v1/health" in logged


def test_apply_retries_health_until_ready_without_waiting_in_the_offline_fixture(tmp_path: Path) -> None:
    """One transient local health failure is retried before release activation succeeds."""
    environment, _root, _env_file, _unit_file, _command_log, _bundle_sha = fixture_environment(tmp_path)
    assert remote(environment, *preflight_arguments(environment)).returncode == 0
    environment.update(
        {
            "TASCA_FAKE_VERSION": "0.1.30",
            "TASCA_FAKE_VIEWER": "true",
            "TASCA_FAKE_LOCAL_HEALTH_FAILURES": "1",
        }
    )

    result = remote(environment, *release_arguments(environment, "apply"))

    assert result.returncode == 0, result.stderr
    assert int(Path(environment["TASCA_FAKE_LOCAL_HEALTH_COUNTER"]).read_text()) == 1
    assert int(Path(environment["TASCA_FAKE_HTTPS_HEALTH_COUNTER"]).read_text()) == 0


def test_apply_retries_https_health_until_ready_without_waiting_in_the_offline_fixture(tmp_path: Path) -> None:
    """One transient HTTPS health failure is retried after local readiness succeeds."""
    environment, _root, _env_file, _unit_file, _command_log, _bundle_sha = fixture_environment(tmp_path)
    assert remote(environment, *preflight_arguments(environment)).returncode == 0
    environment.update(
        {
            "TASCA_FAKE_VERSION": "0.1.30",
            "TASCA_FAKE_VIEWER": "true",
            "TASCA_FAKE_HTTPS_HEALTH_FAILURES": "1",
        }
    )

    result = remote(environment, *release_arguments(environment, "apply"))

    assert result.returncode == 0, result.stderr
    assert int(Path(environment["TASCA_FAKE_LOCAL_HEALTH_COUNTER"]).read_text()) == 0
    assert int(Path(environment["TASCA_FAKE_HTTPS_HEALTH_COUNTER"]).read_text()) == 1


def test_apply_stops_after_bounded_unready_health_attempts(tmp_path: Path) -> None:
    """A service that never becomes ready fails after the fixed health-attempt budget."""
    environment, _root, _env_file, _unit_file, _command_log, _bundle_sha = fixture_environment(tmp_path)
    assert remote(environment, *preflight_arguments(environment)).returncode == 0
    environment.update(
        {
            "TASCA_FAKE_VERSION": "0.1.30",
            "TASCA_FAKE_VIEWER": "true",
            "TASCA_FAKE_LOCAL_HEALTH_FAILURES": "99",
        }
    )

    result = remote(environment, *release_arguments(environment, "apply"))

    assert result.returncode != 0
    assert "local Tasca health is not the expected release" in result.stderr
    assert int(Path(environment["TASCA_FAKE_LOCAL_HEALTH_COUNTER"]).read_text()) == 30


@pytest.mark.parametrize(
    ("omit_viewer", "viewer"),
    [("1", "false"), ("0", "false"), ("0", '"true"')],
)
def test_apply_requires_explicit_boolean_viewer_health_for_0_1_30(
    tmp_path: Path, omit_viewer: str, viewer: str
) -> None:
    """0.1.30 rejects absent, wrong-value, and wrong-type Viewer health fields."""
    environment, _root, _env_file, _unit_file, _command_log, _bundle_sha = fixture_environment(tmp_path)
    assert remote(environment, *preflight_arguments(environment)).returncode == 0
    environment.update(
        {
            "TASCA_FAKE_VERSION": "0.1.30",
            "TASCA_FAKE_VIEWER": viewer,
            "TASCA_FAKE_OMIT_VIEWER": omit_viewer,
        }
    )

    result = remote(environment, *release_arguments(environment, "apply"))

    assert result.returncode != 0
    assert "local Tasca health is not the expected release" in result.stderr


def test_verify_public_read_accepts_legacy_health_and_rechecks_database_identity(tmp_path: Path) -> None:
    """The no-effect public-read verifier accepts old health and detects read-triggered SQLite writes."""
    environment, root, _env_file, _unit_file, command_log, _bundle_sha = fixture_environment(tmp_path)
    assert remote(environment, *preflight_arguments(environment)).returncode == 0
    environment["TASCA_FAKE_OMIT_VIEWER"] = "1"
    arguments = [
        "verify-public-read",
        "--https-host",
        "tasca.example.test",
        "--python",
        environment["TASCA_TEST_PYTHON"],
    ]

    verified = remote(environment, *arguments)

    assert verified.returncode == 0, verified.stderr
    assert "/api/v1/tables" in command_log.read_text()
    assert "secretmanager.googleapis.com" not in command_log.read_text()
    environment["TASCA_MUTATE_DB_ON_TABLE_READ"] = "1"
    rejected = remote(environment, *arguments)

    assert rejected.returncode != 0
    assert "database device, inode, or size changed" in rejected.stderr
    assert (root / "var/lib/tasca/tasca.db").read_bytes().endswith(b"x")


def test_apply_chowns_private_venv_for_tasca_and_keeps_environment_restricted(tmp_path: Path) -> None:
    """A 0700 venv remains executable by tasca through ownership, while the env stays 0640."""
    environment, root, env_file, unit_file, command_log, _bundle_sha = fixture_environment(tmp_path)
    assert remote(environment, *preflight_arguments(environment)).returncode == 0
    environment.update({"TASCA_FAKE_VERSION": "0.1.30", "TASCA_FAKE_VIEWER": "true"})

    result = remote(environment, *release_arguments(environment, "apply"))

    assert result.returncode == 0, result.stderr
    assert "configured Admin/Viewer credentials differ: true" in result.stdout
    log = command_log.read_text()
    assert f"install -d -o tasca -g tasca -m 0700 {root}/opt/tasca/releases/0.1.30" in log
    assert f"chown -R tasca:tasca {root}/opt/tasca/releases/0.1.30/venv" in log
    assert "install -o root -g tasca -m 0640" in log
    assert (root / "opt/tasca/releases/0.1.30/venv/bin/tasca").is_file()
    assert (env_file.stat().st_mode & 0o777) == 0o640
    assert f"ExecStart={root}/opt/tasca/releases/0.1.30/venv/bin/tasca" in unit_file.read_text()


def test_rollback_installs_dependency_complete_bundle_offline_and_keeps_viewer_absent(tmp_path: Path) -> None:
    """Rollback replaces the captured uvx command with exact offline Tasca and httpx bytes."""
    environment, root, env_file, unit_file, _command_log, _bundle_sha = fixture_environment(tmp_path)
    assert remote(environment, *preflight_arguments(environment)).returncode == 0

    result = remote(
        environment,
        "rollback",
        "--rollback-version",
        "0.1.29",
        "--https-host",
        "tasca.example.test",
        "--python",
        environment["TASCA_TEST_PYTHON"],
        "--rollback-bundle",
        environment["TASCA_ROLLBACK_BUNDLE"],
        "--rollback-sha",
        environment["TASCA_ROLLBACK_SHA256"],
    )

    assert result.returncode == 0, result.stderr
    assert "--offline --no-index" in Path(environment["TASCA_UV_LOG"]).read_text()
    assert "--require-hashes" in Path(environment["TASCA_UV_LOG"]).read_text()
    assert "TASCA_VIEWER_TOKEN=" not in env_file.read_text()
    assert f"ExecStart={root}/opt/tasca/releases/0.1.29/venv/bin/tasca" in unit_file.read_text()


def test_preflight_rejects_bundle_digest_mismatch_without_capture(tmp_path: Path) -> None:
    """A changed bundle fails before it can be recorded as rollback input."""
    environment, root, _env_file, _unit_file, _command_log, _bundle_sha = fixture_environment(tmp_path)
    environment["TASCA_ROLLBACK_SHA256"] = "0" * 64

    result = remote(environment, *preflight_arguments(environment))

    assert result.returncode != 0
    assert "rollback bundle digest mismatch" in result.stderr
    assert not (root / "var/lib/tasca/rollback/0.1.29").exists()


def test_reconcile_refuses_mismatched_capture_before_replaying_runtime_effects(tmp_path: Path) -> None:
    """Resume reconciliation reads matching live state and stops when captured disk identity differs."""
    environment, root, _env_file, _unit_file, command_log, _bundle_sha = fixture_environment(tmp_path)
    assert remote(environment, *preflight_arguments(environment)).returncode == 0
    environment.update({"TASCA_FAKE_VERSION": "0.1.30", "TASCA_FAKE_VIEWER": "true"})
    healthy = remote(environment, *release_arguments(environment, "reconcile"))

    assert healthy.returncode == 0, healthy.stderr
    before = command_log.read_text()
    database = root / "var/lib/tasca/tasca.db"
    database.write_bytes(database.read_bytes() + b"changed")
    rejected = remote(environment, *release_arguments(environment, "reconcile"))

    assert rejected.returncode != 0
    assert "database device, inode, or size changed" in rejected.stderr
    after = command_log.read_text()
    assert "systemctl restart tasca.service" not in after[len(before) :]


def test_rollback_rejects_database_changed_by_final_public_table_read(tmp_path: Path) -> None:
    """The post-read identity check catches writes triggered by the required public read."""
    environment, root, _env_file, _unit_file, command_log, _bundle_sha = fixture_environment(tmp_path)
    assert remote(environment, *preflight_arguments(environment)).returncode == 0
    environment["TASCA_MUTATE_DB_ON_TABLE_READ"] = "1"

    result = remote(
        environment,
        "rollback",
        "--rollback-version",
        "0.1.29",
        "--https-host",
        "tasca.example.test",
        "--python",
        environment["TASCA_TEST_PYTHON"],
        "--rollback-bundle",
        environment["TASCA_ROLLBACK_BUNDLE"],
        "--rollback-sha",
        environment["TASCA_ROLLBACK_SHA256"],
    )

    assert result.returncode != 0
    assert "database device, inode, or size changed" in result.stderr
    assert "/api/v1/tables" in command_log.read_text()
    database = root / "var/lib/tasca/tasca.db"
    assert database.read_bytes().endswith(b"x")
