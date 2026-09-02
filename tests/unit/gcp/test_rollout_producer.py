"""Behavioral contracts for the tracked GCP rollout and rollback-bundle producers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parents[3]
ROLLOUT = REPOSITORY / "scripts/gcp/viewer-auth-rollout.sh"
BUNDLE_BUILDER = REPOSITORY / "scripts/gcp/build-viewer-auth-rollback-bundle.sh"
EXPECTED_WHEEL = "tasca-0.1.30-py3-none-any.whl"
ROLLBACK_WHEEL = "tasca-0.1.29-py3-none-any.whl"
NETWORK = "https://www.googleapis.com/compute/v1/projects/rda-engineering/global/networks/tasca-vpc"


def write_wheel(path: Path, *, name: str, version: str, requires: tuple[str, ...] = ()) -> None:
    """Write a metadata-complete wheel fixture without installing package code."""
    metadata = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    if name == "tasca":
        metadata.append("Requires-Python: >=3.13")
    metadata.extend(f"Requires-Dist: {requirement}" for requirement in requires)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA", "\n".join(metadata) + "\n")


def build_rollback_bundle(tmp_path: Path) -> tuple[Path, str, Path]:
    """Build a real deterministic bundle from declared immutable fixture wheels."""
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    rollback_wheel = wheelhouse / ROLLBACK_WHEEL
    httpx_wheel = wheelhouse / "httpx-0.28.1-py3-none-any.whl"
    fastmcp_wheel = wheelhouse / "fastmcp-3.1.0-py3-none-any.whl"
    write_wheel(rollback_wheel, name="tasca", version="0.1.29", requires=("httpx (>=0.28.0)", "fastmcp (>=3.0)"))
    write_wheel(httpx_wheel, name="httpx", version="0.28.1")
    write_wheel(fastmcp_wheel, name="fastmcp", version="3.1.0")
    bundle = tmp_path / "tasca-0.1.29-rollback.tar.gz"
    receipt = tmp_path / "tasca-0.1.29-rollback-receipt.json"
    result = subprocess.run(
        [
            "bash",
            str(BUNDLE_BUILDER),
            "build",
            "--wheel",
            str(rollback_wheel),
            "--wheel-sha256",
            hashlib.sha256(rollback_wheel.read_bytes()).hexdigest(),
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
    assert result.returncode == 0, result.stderr
    return bundle, hashlib.sha256(bundle.read_bytes()).hexdigest(), receipt


def rollout_environment(tmp_path: Path, *, viewer_mode: str | None = None) -> dict[str, str]:
    """Build non-credential exact release and rollback inputs for local producer runs."""
    wheel = tmp_path / EXPECTED_WHEEL
    wheel.write_bytes(b"tasca 0.1.30 exact release fixture")
    rollback_bundle, rollback_sha256, _receipt = build_rollback_bundle(tmp_path)
    environment = os.environ.copy()
    environment.update(
        {
            "PROJECT_ID": "rda-engineering",
            "ZONE": "asia-southeast1-b",
            "VM": "tasca-mcp",
            "TASCA_HTTPS_HOST": "tasca.example.test",
            "TASCA_PYTHON": "/var/lib/tasca/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13",
            "RELEASE_WHEEL": str(wheel),
            "RELEASE_SHA256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "TASCA_ROLLBACK_BUNDLE": str(rollback_bundle),
            "TASCA_ROLLBACK_SHA256": rollback_sha256,
        }
    )
    for name in ("TASCA_ADMIN_TOKEN", "TASCA_VIEWER_TOKEN", "TASCA_VIEWER_MODE"):
        environment.pop(name, None)
    if viewer_mode is not None:
        environment["TASCA_VIEWER_MODE"] = viewer_mode
    return environment


def render_plan(tmp_path: Path, *, digest: str | None = None) -> subprocess.CompletedProcess[str]:
    """Render a release plan with local immutable wheel and bundle fixtures."""
    environment = rollout_environment(tmp_path, viewer_mode="configured")
    if digest is not None:
        environment["RELEASE_SHA256"] = digest
    return subprocess.run(
        [
            "bash",
            str(ROLLOUT),
            "render",
            "--require-version",
            "0.1.30",
            "--rollback-version",
            "0.1.29",
            "--rehearse-rollback",
        ],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def vm_json() -> str:
    """Return the resolved VPC and tag shape used by the real producer."""
    return json.dumps({"networkInterfaces": [{"network": NETWORK}], "tags": {"items": ["tasca-mcp"]}})


def correct_deny_rule() -> str:
    """Return the priority-zero public TCP/8000 denial preserved by resume."""
    return json.dumps(
        {
            "name": "tasca-deny-public-8000",
            "network": NETWORK,
            "direction": "INGRESS",
            "disabled": False,
            "priority": 0,
            "sourceRanges": ["0.0.0.0/0"],
            "targetTags": ["tasca-mcp"],
            "denied": [{"IPProtocol": "tcp", "ports": ["8000"]}],
        }
    )


def stub_commands(directory: Path) -> Path:
    """Install a transport stub that records every prospective GCP effect."""
    log = directory / "gcloud.log"
    gcloud = directory / "gcloud"
    gcloud.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$GCP_LOG\"\n"
        "case \"$*\" in *\"${GCP_FAIL_COMMAND:-__no_failure__}\"*) exit 1 ;; esac\n"
        "case \"$*\" in\n"
        "  *'instances describe'*) printf '%s\\n' \"$GCP_VM_JSON\" ;;\n"
        "  *'firewall-rules list'*) printf '%s\\n' \"${GCP_FIREWALL_LIST_JSON:-[]}\" ;;\n"
        "  *'firewall-rules create'*) : > \"$GCP_CREATE_DONE\" ;;\n"
        "  *'firewall-rules describe'*)\n"
        "    [ \"${GCP_FIREWALL_EXISTS:-1}\" = 1 ] || exit 1\n"
        "    if [ -f \"$GCP_CREATE_DONE\" ]; then printf '%s\\n' \"$GCP_FIREWALL_AFTER_CREATE_JSON\"; else printf '%s\\n' \"$GCP_FIREWALL_JSON\"; fi ;;\n"
        "  *'viewer-auth-remote.sh reseal-sqlite-logical-state'*) [ \"${GCP_RESEAL_SQLITE_READY:-0}\" = 1 ] || exit 1 ;;\n"
        "  *'viewer-auth-remote.sh verify-public-read'*) [ \"${GCP_PUBLIC_ROLLBACK_READY:-0}\" = 1 ] || exit 1 ;;\n"
        "  *'secrets versions list tasca-viewer-token'*) printf '%s\\n' \"${GCP_VIEWER_ENABLED-1}\" ;;\n"
        "  *'secrets versions list tasca-admin-token'*)\n"
        "    if [ -n \"${GCP_ADMIN_STATE_FILE:-}\" ]; then cat \"$GCP_ADMIN_STATE_FILE\"; else printf '%s\\n' \"${GCP_ADMIN_ENABLED-1}\"; fi ;;\n"
        "  *'secrets versions add tasca-admin-token'*)\n"
        "    created=\"${GCP_ADMIN_CREATED-2}\"\n"
        "    printf '%s\\n' \"$created\"\n"
        "    if [ -n \"${GCP_ADMIN_STATE_FILE:-}\" ]; then printf '%s\\n' \"$created\" >> \"$GCP_ADMIN_STATE_FILE\"; fi ;;\n"
        "  *'secrets versions disable 1'*)\n"
        "    if [ -n \"${GCP_ADMIN_STATE_FILE:-}\" ]; then grep -vx '1' \"$GCP_ADMIN_STATE_FILE\" > \"$GCP_ADMIN_STATE_FILE.next\"; mv \"$GCP_ADMIN_STATE_FILE.next\" \"$GCP_ADMIN_STATE_FILE\"; fi ;;\n"
        "esac\n"
    )
    git = directory / "git"
    real_git = shutil.which("git")
    assert real_git is not None
    git.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in *'rev-parse'*) exec \"$REAL_GIT\" \"$@\" ;; *) exit 0 ;; esac\n"
    )
    gcloud.chmod(0o755)
    git.chmod(0o755)
    log.write_text("")
    return log


def run_action(
    tmp_path: Path,
    action: str,
    *,
    firewall: str | None = None,
    after_create: str | None = None,
    rules: list[dict[str, object]] | None = None,
    rotate_admin: bool = False,
    admin_enabled: str | None = None,
    admin_created: str | None = None,
    viewer_enabled: str | None = None,
    admin_state: Path | None = None,
    verification_state: Path | None = None,
    failure: str | None = None,
    public_rollback_ready: bool = False,
    reseal_sqlite_ready: bool = False,
    accept_current_sqlite_logical_state: bool = False,
    rehearse: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run one producer action through the transport seam and return call order."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    log = stub_commands(bin_dir)
    environment = rollout_environment(tmp_path, viewer_mode="configured")
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
            "GCP_LOG": str(log),
            "GCP_VM_JSON": vm_json(),
            "GCP_FIREWALL_JSON": firewall or correct_deny_rule(),
            "GCP_FIREWALL_AFTER_CREATE_JSON": after_create or correct_deny_rule(),
            "GCP_FIREWALL_LIST_JSON": json.dumps(rules or []),
            "GCP_CREATE_DONE": str(tmp_path / "created"),
            "GCP_FIREWALL_EXISTS": "1",
            "GCP_ADMIN_ENABLED": admin_enabled if admin_enabled is not None else "1",
            "GCP_ADMIN_CREATED": admin_created if admin_created is not None else "2",
            "GCP_VIEWER_ENABLED": viewer_enabled if viewer_enabled is not None else "1",
            "GCP_PUBLIC_ROLLBACK_READY": "1" if public_rollback_ready else "0",
            "GCP_RESEAL_SQLITE_READY": "1" if reseal_sqlite_ready else "0",
        }
    )
    if admin_state is not None:
        environment["GCP_ADMIN_STATE_FILE"] = str(admin_state)
    if failure is not None:
        environment["GCP_FAIL_COMMAND"] = failure
    command = [
        "bash",
        str(ROLLOUT),
        action,
        "--require-version",
        "0.1.30",
        "--rollback-version",
        "0.1.29",
    ]
    if action == "resume" and rehearse:
        command.append("--rehearse-rollback")
    if verification_state is not None:
        command.extend(("--verification-state", str(verification_state)))
    if rotate_admin:
        command.append("--rotate-admin-secret-version")
    if accept_current_sqlite_logical_state:
        command.append("--accept-current-sqlite-logical-state")
    result = subprocess.run(command, cwd=REPOSITORY, env=environment, text=True, capture_output=True, check=False)
    return result, log.read_text().splitlines()


def test_bundle_is_deterministic_and_receipt_binds_exact_bytes_dependencies_and_producer(tmp_path: Path) -> None:
    """A rollback bundle records exact bytes, httpx, Python requirement, and producer identity."""
    bundle, digest, receipt = build_rollback_bundle(tmp_path)
    duplicate = tmp_path / "duplicate.tar.gz"
    duplicate_receipt = tmp_path / "duplicate.json"
    wheelhouse = tmp_path / "wheelhouse"
    wheel = wheelhouse / ROLLBACK_WHEEL
    second = subprocess.run(
        [
            "bash",
            str(BUNDLE_BUILDER),
            "build",
            "--wheel",
            str(wheel),
            "--wheel-sha256",
            hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "--wheelhouse",
            str(wheelhouse),
            "--output",
            str(duplicate),
            "--receipt",
            str(duplicate_receipt),
        ],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        check=False,
    )

    assert second.returncode == 0, second.stderr
    assert bundle.read_bytes() == duplicate.read_bytes()
    payload = json.loads(receipt.read_text())
    manifest = payload["manifest"]
    assert payload["bundle"]["sha256"] == digest
    assert manifest["rollback"] == {
        "constraints": {"fastmcp": "<4", "httpx": "required"},
        "python_requires": ">=3.13",
        "version": "0.1.29",
        "wheel": ROLLBACK_WHEEL,
        "wheel_sha256": hashlib.sha256((wheelhouse / ROLLBACK_WHEEL).read_bytes()).hexdigest(),
    }
    assert {artifact["name"] for artifact in manifest["artifacts"]} == {"tasca", "httpx", "fastmcp"}
    assert len(manifest["producer"]["revision"]) == 40
    assert all(len(item["sha256"]) == 64 for item in manifest["producer"]["files"])
    assert "TASCA_VIEWER_TOKEN" not in receipt.read_text()


def test_bundle_rejects_incompatible_declared_dependency_bytes(tmp_path: Path) -> None:
    """A wheelhouse with httpx below Tasca's declared range cannot become a rollback input."""
    wheelhouse = tmp_path / "incompatible-wheelhouse"
    wheelhouse.mkdir()
    tasca = wheelhouse / ROLLBACK_WHEEL
    write_wheel(tasca, name="tasca", version="0.1.29", requires=("httpx (>=0.28.0)",))
    write_wheel(wheelhouse / "httpx-0.27.0-py3-none-any.whl", name="httpx", version="0.27.0")

    result = subprocess.run(
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
            str(tmp_path / "bundle.tar.gz"),
            "--receipt",
            str(tmp_path / "receipt.json"),
        ],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "incompatible declared dependencies" in result.stderr


def test_bundle_rejects_fastmcp_4_even_if_the_tasca_metadata_allows_it(tmp_path: Path) -> None:
    """The rollback contract pins the FastMCP major line rather than trusting ambient resolution."""
    wheelhouse = tmp_path / "fastmcp-4-wheelhouse"
    wheelhouse.mkdir()
    tasca = wheelhouse / ROLLBACK_WHEEL
    write_wheel(tasca, name="tasca", version="0.1.29", requires=("httpx (>=0.28.0)", "fastmcp (>=3.0)"))
    write_wheel(wheelhouse / "httpx-0.28.1-py3-none-any.whl", name="httpx", version="0.28.1")
    write_wheel(wheelhouse / "fastmcp-4.0.0-py3-none-any.whl", name="fastmcp", version="4.0.0")

    result = subprocess.run(
        [
            "bash", str(BUNDLE_BUILDER), "build", "--wheel", str(tasca),
            "--wheel-sha256", hashlib.sha256(tasca.read_bytes()).hexdigest(),
            "--wheelhouse", str(wheelhouse), "--output", str(tmp_path / "bundle.tar.gz"),
            "--receipt", str(tmp_path / "receipt.json"),
        ],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "fastmcp version constrained to <4" in result.stderr


def test_render_binds_exact_release_rollback_bundle_and_python_selection(tmp_path: Path) -> None:
    """The renderer binds the exact 0.1.30 wheel, repair receipt, and CPython selector."""
    result = render_plan(tmp_path)

    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["release"]["wheel"] == EXPECTED_WHEEL
    assert manifest["rollback"]["version"] == "0.1.29"
    assert manifest["python"] == {"selector": "TASCA_PYTHON", "required": "CPython 3.13"}
    assert "stage_certificate_valid_https_without_backend_health" in manifest["actions"]
    assert manifest["actions"][-3:] == [
        "install_offline_0_1_29_bundle",
        "restore_0_1_29_public_read",
        "reinstall_exact_0_1_30_wheel",
    ]


@pytest.mark.parametrize("digest", ["0" * 64, "broken"])
def test_render_rejects_bad_release_identity_before_any_transport(tmp_path: Path, digest: str) -> None:
    """A mismatched release digest cannot become an input to a runtime command."""
    result = render_plan(tmp_path, digest=digest)

    assert result.returncode != 0
    assert "RELEASE_SHA256" in result.stderr


def test_resume_refuses_system_python_before_any_gcloud_effect(tmp_path: Path) -> None:
    """The explicit interpreter selector rejects Debian's system Python before transport."""
    environment = rollout_environment(tmp_path)
    environment["TASCA_PYTHON"] = "/usr/bin/python3"
    result = subprocess.run(
        ["bash", str(ROLLOUT), "resume", "--require-version", "0.1.30", "--rollback-version", "0.1.29"],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "non-system absolute interpreter" in result.stderr


def test_apply_preflights_selected_python_and_bundle_before_tls_or_secret_effects(tmp_path: Path) -> None:
    """Apply invokes the remote Python/capture gate before TLS, firewall, and credential work."""
    result, commands = run_action(tmp_path, "apply")

    assert result.returncode == 0, result.stderr
    preflight = next(index for index, command in enumerate(commands) if "viewer-auth-remote.sh preflight" in command)
    tls = next(index for index, command in enumerate(commands) if "viewer-auth-remote.sh stage-tls" in command)
    assert preflight < tls
    assert "--python /var/lib/tasca/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13" in commands[preflight]
    assert any("compute scp" in command and "rollback.tar.gz" in command for command in commands)


def test_resume_creates_one_numeric_admin_v2_after_reconciliation(tmp_path: Path) -> None:
    """One enabled numeric v1 creates one numeric v2 through stdin after the rehearsal setup."""
    result, commands = run_action(
        tmp_path, "resume", rotate_admin=True, admin_enabled="1", admin_created="2"
    )

    assert result.returncode == 0, result.stderr
    assert any("viewer-auth-remote.sh verify-public-read" in command for command in commands)
    assert any("viewer-auth-remote.sh reconcile" in command for command in commands)
    assert any("viewer-auth-remote.sh rehearse" in command for command in commands)
    formatter = "--format=value(name.basename())"
    viewer_list = next(command for command in commands if "secrets versions list tasca-viewer-token" in command)
    admin_list = next(command for command in commands if "secrets versions list tasca-admin-token" in command)
    additions = [command for command in commands if "secrets versions add tasca-admin-token" in command]
    assert formatter in viewer_list
    assert formatter in admin_list
    assert len(additions) == 1
    assert "--data-file=-" in additions[0]
    assert formatter in additions[0]
    assert any("secrets versions disable 1" in command for command in commands)
    assert not any("instances add-tags" in command for command in commands)
    assert not any("firewall-rules create" in command or "firewall-rules delete" in command for command in commands)
    assert not any("viewer-auth-remote.sh stage-tls" in command for command in commands)
    assert not any("secrets versions add tasca-viewer-token" in command for command in commands)


def test_resume_recovers_reconciled_public_0_1_29_with_existing_admin_v2(tmp_path: Path) -> None:
    """A verified public rollback re-applies 0.1.30 and retires v1 without creating v3."""
    admin_state = tmp_path / "admin-enabled.txt"
    admin_state.write_text("1\n2\n")

    result, commands = run_action(
        tmp_path,
        "resume",
        rotate_admin=True,
        admin_state=admin_state,
        public_rollback_ready=True,
        reseal_sqlite_ready=True,
        accept_current_sqlite_logical_state=True,
    )

    assert result.returncode == 0, result.stderr
    reseal = next(
        index for index, command in enumerate(commands) if "viewer-auth-remote.sh reseal-sqlite-logical-state" in command
    )
    verification = next(
        index for index, command in enumerate(commands) if "viewer-auth-remote.sh verify-public-read" in command
    )
    activation = next(index for index, command in enumerate(commands) if "viewer-auth-remote.sh apply" in command)
    disabled = next(index for index, command in enumerate(commands) if "secrets versions disable 1" in command)
    assert reseal < verification < activation < disabled
    assert not any("viewer-auth-remote.sh reconcile" in command for command in commands)
    assert not any("viewer-auth-remote.sh rehearse" in command for command in commands)
    assert not any("viewer-auth-remote.sh rollback" in command for command in commands)
    assert not any("secrets versions add tasca-admin-token" in command for command in commands)
    assert admin_state.read_text() == "2\n"


def test_resume_stops_when_explicit_sqlite_reseal_fails_before_reapply_or_disable(tmp_path: Path) -> None:
    """A failed one-time logical-state proof cannot reach verification, activation, or secret mutation."""
    result, commands = run_action(
        tmp_path,
        "resume",
        rotate_admin=True,
        public_rollback_ready=True,
        accept_current_sqlite_logical_state=True,
    )

    assert result.returncode != 0
    assert any("viewer-auth-remote.sh reseal-sqlite-logical-state" in command for command in commands)
    assert not any("viewer-auth-remote.sh verify-public-read" in command for command in commands)
    assert not any("viewer-auth-remote.sh apply" in command for command in commands)
    assert not any("secrets versions add tasca-admin-token" in command for command in commands)
    assert not any("secrets versions disable" in command for command in commands)


def test_sqlite_logical_state_acceptance_flag_is_resume_only(tmp_path: Path) -> None:
    """The recovery authorization cannot be attached to a fresh apply action."""
    result, commands = run_action(
        tmp_path,
        "apply",
        accept_current_sqlite_logical_state=True,
    )

    assert result.returncode != 0
    assert "apply accepts no reapply, rotation, or SQLite logical-state acceptance options" in result.stderr
    assert commands == []


def test_resume_rejects_public_rollback_recovery_without_rehearsal_authority(tmp_path: Path) -> None:
    """Recovery requires explicit rehearsal authority before any Admin or release mutation."""
    result, commands = run_action(
        tmp_path,
        "resume",
        rotate_admin=True,
        admin_enabled="1\n2",
        public_rollback_ready=True,
        rehearse=False,
    )

    assert result.returncode != 0
    assert "recovery from exact 0.1.29 public-read state requires --rehearse-rollback" in result.stderr
    assert any("viewer-auth-remote.sh verify-public-read" in command for command in commands)
    assert not any("viewer-auth-remote.sh apply" in command for command in commands)
    assert not any("secrets versions add tasca-admin-token" in command for command in commands)
    assert not any("secrets versions disable" in command for command in commands)


def test_resume_unknown_state_fails_before_admin_rotation_or_rehearsal(tmp_path: Path) -> None:
    """A failed public-state check followed by failed reconciliation cannot replay rollout effects."""
    result, commands = run_action(
        tmp_path,
        "resume",
        rotate_admin=True,
        failure="viewer-auth-remote.sh reconcile",
    )

    assert result.returncode != 0
    assert any("viewer-auth-remote.sh verify-public-read" in command for command in commands)
    assert any("viewer-auth-remote.sh reconcile" in command for command in commands)
    assert not any("viewer-auth-remote.sh rehearse" in command for command in commands)
    assert not any("secrets versions add tasca-admin-token" in command for command in commands)
    assert not any("secrets versions disable" in command for command in commands)


@pytest.mark.parametrize(
    "admin_enabled",
    [
        pytest.param("", id="empty"),
        pytest.param("0", id="zero"),
        pytest.param("1\n1", id="duplicate-v1"),
        pytest.param("1\n2\n3", id="more-than-two"),
        pytest.param("2\n3", id="two-post-v1-without-v1"),
        pytest.param("projects/rda-engineering/secrets/tasca-admin-token/versions/1", id="malformed-resource-name"),
    ],
)
def test_resume_rejects_invalid_numeric_admin_enabled_versions_before_secret_write(
    tmp_path: Path, admin_enabled: str
) -> None:
    """Admin rotation rejects empty, malformed, duplicate, and ambiguous value(name) output."""
    result, commands = run_action(tmp_path, "resume", rotate_admin=True, admin_enabled=admin_enabled)

    assert result.returncode != 0
    assert "Admin rotation" in result.stderr
    assert not any("secrets versions add" in command for command in commands)
    assert not any("secrets versions disable" in command for command in commands)


@pytest.mark.parametrize(
    "admin_created",
    [
        pytest.param("0", id="zero"),
        pytest.param("2\n3", id="multiple"),
        pytest.param("projects/rda-engineering/secrets/tasca-admin-token/versions/2", id="malformed-resource-name"),
    ],
)
def test_resume_rejects_malformed_numeric_admin_creation_output(
    tmp_path: Path, admin_created: str
) -> None:
    """A v1 rotation refuses malformed value(name) output after one attempted stdin creation."""
    result, commands = run_action(
        tmp_path, "resume", rotate_admin=True, admin_enabled="1", admin_created=admin_created
    )

    assert result.returncode != 0
    assert "Admin rotation did not create a new named secret version" in result.stderr
    assert sum("secrets versions add tasca-admin-token" in command for command in commands) == 1
    assert not any("secrets versions disable" in command for command in commands)


def test_reapply_marks_only_a_token_free_prepared_persistence_receipt(tmp_path: Path) -> None:
    """The restart marks the bounded witness only after its remote activation succeeds."""
    receipt = tmp_path / "persistence.json"
    receipt.write_text(
        json.dumps(
            {
                "format": "tasca-viewer-auth-persistence-v1",
                "phase": "prepared",
                "base_url": "https://tasca.example.test",
                "expected_version": "0.1.30",
                "table_id": "fixture-table",
            }
        )
    )
    receipt.chmod(0o600)

    result, commands = run_action(tmp_path, "reapply", verification_state=receipt)

    assert result.returncode == 0, result.stderr
    assert json.loads(receipt.read_text())["phase"] == "reapplied"
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert "token" not in receipt.read_text().lower()
    assert any("viewer-auth-remote.sh apply" in command for command in commands)


def test_reapply_keeps_prepare_receipt_when_remote_activation_fails(tmp_path: Path) -> None:
    """A failed reapply cannot report persistence verification as complete."""
    receipt = tmp_path / "persistence.json"
    receipt.write_text(
        json.dumps(
            {
                "format": "tasca-viewer-auth-persistence-v1",
                "phase": "prepared",
                "base_url": "https://tasca.example.test",
                "expected_version": "0.1.30",
                "table_id": "fixture-table",
            }
        )
    )
    receipt.chmod(0o600)

    result, _commands = run_action(
        tmp_path,
        "reapply",
        verification_state=receipt,
        failure="viewer-auth-remote.sh apply",
    )

    assert result.returncode != 0
    assert json.loads(receipt.read_text())["phase"] == "prepared"


def test_apply_replaces_wrong_vpc_or_lower_precedence_named_deny_without_touching_allows(tmp_path: Path) -> None:
    """Apply recreates only the named deny when it cannot preempt public TCP/8000 on the VM VPC."""
    wrong_vpc = json.loads(correct_deny_rule())
    wrong_vpc["network"] = "https://www.googleapis.com/compute/v1/projects/rda-engineering/global/networks/wrong-vpc"
    public_allow = {
        "name": "allow-public-8000",
        "network": NETWORK,
        "direction": "INGRESS",
        "disabled": False,
        "priority": 800,
        "sourceRanges": ["0.0.0.0/0"],
        "targetTags": ["tasca-mcp"],
        "allowed": [{"IPProtocol": "tcp", "ports": ["7000-9000"]}],
    }

    result, commands = run_action(
        tmp_path, "apply", firewall=json.dumps(wrong_vpc), rules=[public_allow]
    )

    assert result.returncode == 0, result.stderr
    deleted = next(index for index, item in enumerate(commands) if "firewall-rules delete tasca-deny-public-8000" in item)
    created = next(index for index, item in enumerate(commands) if "firewall-rules create tasca-deny-public-8000" in item)
    assert deleted < created
    assert f"--network={NETWORK}" in commands[created]
    assert "--priority=0" in commands[created]
    assert "tcp:8000" in commands[created]
    assert not any("firewall-rules delete allow-public-8000" in item for item in commands)


def test_apply_rejects_created_deny_that_fails_target_vpc_readback(tmp_path: Path) -> None:
    """A successful create is insufficient until the named rule reads back as the intended deny."""
    wrong_vpc = json.loads(correct_deny_rule())
    wrong_vpc["network"] = "https://www.googleapis.com/compute/v1/projects/rda-engineering/global/networks/wrong-vpc"
    malformed = json.loads(correct_deny_rule())
    malformed["sourceRanges"] = ["10.0.0.0/8"]

    result, _commands = run_action(
        tmp_path,
        "apply",
        firewall=json.dumps(wrong_vpc),
        after_create=json.dumps(malformed),
    )

    assert result.returncode != 0
    assert "does not match the target VPC contract" in result.stderr


def test_resume_accepts_gcloud_value_name_viewer_v1(tmp_path: Path) -> None:
    """The real gcloud value(name) response identifies one enabled Viewer version as 1."""
    result, commands = run_action(tmp_path, "resume", viewer_enabled="1")

    assert result.returncode == 0, result.stderr
    assert any("compute scp" in command for command in commands)


@pytest.mark.parametrize(
    "viewer_enabled",
    [
        pytest.param("", id="none"),
        pytest.param("2", id="version-2"),
        pytest.param("1\n2", id="multiple"),
        pytest.param("projects/rda-engineering/secrets/tasca-viewer-token/versions/1", id="malformed-resource-name"),
    ],
)
def test_resume_rejects_viewer_secret_layout_other_than_exact_enabled_v1(
    tmp_path: Path, viewer_enabled: str
) -> None:
    """Resume rejects absent, ambiguous, wrong, and malformed value(name) output before staging."""
    result, commands = run_action(tmp_path, "resume", viewer_enabled=viewer_enabled)

    assert result.returncode != 0
    assert "Viewer secret version" in result.stderr
    assert not any("compute scp" in command for command in commands)


def test_resume_reuses_numeric_partial_admin_rotation_after_rehearsal_failure(tmp_path: Path) -> None:
    """A second resume reuses numeric v2 and disables v1 only after a successful rehearsal."""
    state = tmp_path / "enabled-admin-versions"
    state.write_text("1\n")
    failed, failed_commands = run_action(
        tmp_path / "first",
        "resume",
        rotate_admin=True,
        admin_state=state,
        failure="viewer-auth-remote.sh rehearse",
    )

    assert failed.returncode != 0
    assert any("secrets versions add tasca-admin-token" in command for command in failed_commands)
    assert not any("secrets versions disable 1" in command for command in failed_commands)
    assert state.read_text().splitlines() == ["1", "2"]

    resumed, resumed_commands = run_action(
        tmp_path / "second", "resume", rotate_admin=True, admin_state=state
    )

    assert resumed.returncode == 0, resumed.stderr
    assert "reusing the existing post-version-1 Admin secret" in resumed.stdout
    assert not any("secrets versions add tasca-admin-token" in command for command in resumed_commands)
    rehearsal = next(index for index, command in enumerate(resumed_commands) if "viewer-auth-remote.sh rehearse" in command)
    disable = next(index for index, command in enumerate(resumed_commands) if "secrets versions disable 1" in command)
    assert rehearsal < disable
    assert state.read_text().splitlines() == ["2"]


def test_resume_treats_one_enabled_post_v1_admin_version_as_finalized(tmp_path: Path) -> None:
    """A retried finalized rotation runs reconciliation without another secret mutation."""
    state = tmp_path / "enabled-admin-versions"
    state.write_text("10\n")

    result, commands = run_action(tmp_path / "finalized", "resume", rotate_admin=True, admin_state=state)

    assert result.returncode == 0, result.stderr
    assert "Admin rotation is already finalized at enabled version 10" in result.stdout
    assert not any("secrets versions add tasca-admin-token" in command for command in commands)
    assert not any("secrets versions disable" in command for command in commands)


def test_resume_refuses_mismatched_firewall_before_staging_or_rotation(tmp_path: Path) -> None:
    """Resume stops at read-only reconciliation when the completed deny effect differs."""
    mismatched = json.loads(correct_deny_rule())
    mismatched["priority"] = 900
    result, commands = run_action(tmp_path, "resume", firewall=json.dumps(mismatched), rotate_admin=True)

    assert result.returncode != 0
    assert "mismatched TCP/8000 deny rule" in result.stderr
    assert not any("compute scp" in command for command in commands)
    assert not any("secrets versions add" in command for command in commands)
