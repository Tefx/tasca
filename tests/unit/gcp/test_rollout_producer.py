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
    write_wheel(rollback_wheel, name="tasca", version="0.1.29", requires=("httpx (>=0.28.0)",))
    write_wheel(httpx_wheel, name="httpx", version="0.28.1")
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
        "case \"$*\" in\n"
        "  *'instances describe'*) printf '%s\\n' \"$GCP_VM_JSON\" ;;\n"
        "  *'firewall-rules describe'*) printf '%s\\n' \"$GCP_FIREWALL_JSON\" ;;\n"
        "  *'secrets versions describe 1'*) printf 'ENABLED\\n' ;;\n"
        "  *'secrets versions list'*) printf '%s\\n' \"${GCP_ADMIN_ENABLED:-projects/rda-engineering/secrets/tasca-admin-token/versions/1}\" ;;\n"
        "  *'secrets versions add'*) printf 'projects/rda-engineering/secrets/tasca-admin-token/versions/2\\n' ;;\n"
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
    rotate_admin: bool = False,
    admin_enabled: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run one producer action through the transport seam and return call order."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = stub_commands(bin_dir)
    environment = rollout_environment(tmp_path, viewer_mode="configured")
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
            "GCP_LOG": str(log),
            "GCP_VM_JSON": vm_json(),
            "GCP_FIREWALL_JSON": firewall or correct_deny_rule(),
            "GCP_ADMIN_ENABLED": admin_enabled or "projects/rda-engineering/secrets/tasca-admin-token/versions/1",
        }
    )
    command = [
        "bash",
        str(ROLLOUT),
        action,
        "--require-version",
        "0.1.30",
        "--rollback-version",
        "0.1.29",
    ]
    if action == "resume":
        command.append("--rehearse-rollback")
    if rotate_admin:
        command.append("--rotate-admin-secret-version")
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
        "python_requires": ">=3.13",
        "version": "0.1.29",
        "wheel": ROLLBACK_WHEEL,
        "wheel_sha256": hashlib.sha256((wheelhouse / ROLLBACK_WHEEL).read_bytes()).hexdigest(),
    }
    assert {artifact["name"] for artifact in manifest["artifacts"]} == {"tasca", "httpx"}
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


def test_resume_preserves_matching_tls_firewall_and_viewer_secret_while_rotating_admin_once(tmp_path: Path) -> None:
    """Resume reads matching effects, rehearses only runtime bytes, and rotates Admin once."""
    result, commands = run_action(tmp_path, "resume", rotate_admin=True)

    assert result.returncode == 0, result.stderr
    assert any("viewer-auth-remote.sh reconcile" in command for command in commands)
    assert any("viewer-auth-remote.sh rehearse" in command for command in commands)
    assert any("secrets versions add tasca-admin-token" in command for command in commands)
    assert any("secrets versions disable 1" in command for command in commands)
    assert not any("instances add-tags" in command for command in commands)
    assert not any("firewall-rules create" in command or "firewall-rules delete" in command for command in commands)
    assert not any("viewer-auth-remote.sh stage-tls" in command for command in commands)
    assert not any("secrets versions add tasca-viewer-token" in command for command in commands)


def test_resume_refuses_a_non_one_time_admin_rotation_before_secret_write(tmp_path: Path) -> None:
    """A second enabled Admin version blocks the named rotation before it can add another."""
    result, commands = run_action(
        tmp_path,
        "resume",
        rotate_admin=True,
        admin_enabled="projects/rda-engineering/secrets/tasca-admin-token/versions/1\nprojects/rda-engineering/secrets/tasca-admin-token/versions/2",
    )

    assert result.returncode != 0
    assert "one-time and requires only enabled version 1" in result.stderr
    assert not any("secrets versions add" in command for command in commands)
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
