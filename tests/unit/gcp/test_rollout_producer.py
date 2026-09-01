"""Behavioral contracts for the tracked GCP rollout producer."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parents[3]
ROLLOUT = REPOSITORY / "scripts/gcp/viewer-auth-rollout.sh"
EXPECTED_WHEEL = "tasca-0.1.30-py3-none-any.whl"
NETWORK = "https://www.googleapis.com/compute/v1/projects/rda-engineering/global/networks/tasca-vpc"


def rollout_environment(tmp_path: Path, *, viewer_mode: str | None = None) -> dict[str, str]:
    """Build a non-credential release environment for a local renderer or stub."""
    wheel = tmp_path / EXPECTED_WHEEL
    wheel.write_bytes(b"tasca release contract fixture")
    environment = os.environ.copy()
    environment.update(
        {
            "PROJECT_ID": "rda-engineering",
            "ZONE": "asia-southeast1-b",
            "VM": "tasca-mcp",
            "TASCA_HTTPS_HOST": "tasca.example.test",
            "RELEASE_WHEEL": str(wheel),
            "RELEASE_SHA256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        }
    )
    for name in ("TASCA_ADMIN_TOKEN", "TASCA_VIEWER_TOKEN", "TASCA_VIEWER_MODE"):
        environment.pop(name, None)
    if viewer_mode is not None:
        environment["TASCA_VIEWER_MODE"] = viewer_mode
    return environment


def render_plan(
    tmp_path: Path,
    *,
    filename: str = EXPECTED_WHEEL,
    digest: str | None = None,
    viewer_mode: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Render a release plan with a local, non-credential wheel fixture."""
    environment = rollout_environment(tmp_path, viewer_mode=viewer_mode)
    wheel = Path(environment["RELEASE_WHEEL"])
    if filename != EXPECTED_WHEEL:
        renamed = tmp_path / filename
        wheel.rename(renamed)
        wheel = renamed
        environment["RELEASE_WHEEL"] = str(wheel)
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


def test_render_binds_exact_wheel_hash_tracked_producer_and_verifier(tmp_path: Path) -> None:
    """The renderer binds both rollout producers and its exact release identity."""
    result = render_plan(tmp_path)

    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["release"] == {
        "version": "0.1.30",
        "wheel": EXPECTED_WHEEL,
        "sha256": hashlib.sha256(b"tasca release contract fixture").hexdigest(),
    }
    assert len(manifest["producer"]["revision"]) == 40
    assert len(manifest["producer"]["sha256"]) == 64
    assert manifest["verifier"]["path"] == "scripts/gcp/verify_viewer_auth_remote.py"
    assert manifest["verifier"]["revision"] == manifest["producer"]["revision"]
    assert len(manifest["verifier"]["sha256"]) == 64
    assert manifest["persistence"] == {
        "protocol": "prepare-rollout-reapply-cleanup",
        "state": "0600-token-free",
    }


@pytest.mark.parametrize(
    ("viewer_mode", "expected_mode", "expected_credential"),
    [(None, "public", "absent"), ("clear", "public", "absent"), ("configured", "configured", "redacted")],
)
def test_render_selects_optional_viewer_mode_without_credential_values(
    tmp_path: Path, viewer_mode: str | None, expected_mode: str, expected_credential: str
) -> None:
    """Absent/clear Viewer mode stays public; configured mode schedules a redacted fetch."""
    result = render_plan(tmp_path, viewer_mode=viewer_mode)

    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["viewer"] == {"mode": expected_mode, "credential": expected_credential}
    fetch_action = "fetch_redacted_viewer_secret_after_tls"
    assert (fetch_action in manifest["actions"]) is (expected_mode == "configured")


def test_render_orders_tls_and_effective_port_reconciliation_before_configured_viewer_fetch(
    tmp_path: Path,
) -> None:
    """Configured Viewer credentials wait for HTTPS and effective TCP/8000 closure."""
    result = render_plan(tmp_path, viewer_mode="configured")
    assert result.returncode == 0, result.stderr

    manifest = json.loads(result.stdout)
    actions = manifest["actions"]
    assert actions.index("stage_certificate_valid_https") < actions.index(
        "resolve_vm_vpc_and_reconcile_effective_public_tcp_8000"
    ) < actions.index("fetch_redacted_viewer_secret_after_tls")
    assert manifest["backend"] == {"bind": "127.0.0.1:8000", "writers": 1}


def test_render_includes_exact_rollback_without_sqlite_mutation(tmp_path: Path) -> None:
    """A rehearsal plan restores the prior public state with identity checks, not DB writes."""
    result = render_plan(tmp_path)
    assert result.returncode == 0, result.stderr

    manifest = json.loads(result.stdout)
    assert manifest["actions"][-2:] == [
        "restore_0_1_29_public_read",
        "reinstall_exact_0_1_30_wheel",
    ]
    assert manifest["database"] == {"device": "tasca-data", "sqlite_bytes": "identity-checked"}


@pytest.mark.parametrize("filename", ["tasca-0.1.29-py3-none-any.whl", "tasca.whl"])
def test_render_rejects_non_release_wheel_filename(tmp_path: Path, filename: str) -> None:
    """A same-byte but differently named wheel cannot become the release input."""
    result = render_plan(tmp_path, filename=filename)

    assert result.returncode != 0
    assert "release wheel filename" in result.stderr


def test_render_rejects_hash_mismatch(tmp_path: Path) -> None:
    """A valid filename still fails before any GCP command when its digest differs."""
    result = render_plan(tmp_path, digest="0" * 64)

    assert result.returncode != 0
    assert "does not match the release wheel" in result.stderr


def vm_json() -> str:
    """Return the VM's resolved VPC and tags as the real producer consumes them."""
    return json.dumps({"networkInterfaces": [{"network": NETWORK}], "tags": {"items": ["tasca-mcp"]}})


def correct_deny_rule() -> str:
    """Return the exact VPC-bound deny rule the producer must preserve."""
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
    """Install gcloud/git stubs whose JSON responses model network-level policy."""
    log = directory / "gcloud.log"
    gcloud = directory / "gcloud"
    gcloud.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$GCP_LOG\"\n"
        "case \"$*\" in *\"${GCP_FAIL_COMMAND:-__no_failure__}\"*) exit 1 ;; esac\n"
        "case \"$*\" in\n"
        "  *'instances describe'*) printf '%s\\n' \"$GCP_VM_JSON\" ;;\n"
        "  *'firewall-rules list'*) printf '%s\\n' \"$GCP_FIREWALL_LIST_JSON\" ;;\n"
        "  *'firewall-rules create'*) : > \"$GCP_CREATE_DONE\" ;;\n"
        "  *'firewall-rules describe'*)\n"
        "    [ \"${GCP_FIREWALL_EXISTS:-1}\" = 1 ] || exit 1\n"
        "    if [ -f \"$GCP_CREATE_DONE\" ]; then printf '%s\\n' \"$GCP_FIREWALL_AFTER_CREATE_JSON\"; else printf '%s\\n' \"$GCP_FIREWALL_JSON\"; fi ;;\n"
        "esac\n"
    )
    git = directory / "git"
    git.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *'rev-parse'*) printf '%040d\\n' 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    gcloud.chmod(0o755)
    git.chmod(0o755)
    log.write_text("")
    return log


def apply_with_stub(
    tmp_path: Path,
    *,
    firewall: str,
    rules: list[dict[str, object]] | None = None,
    action: str = "apply",
    verification_state: Path | None = None,
    after_create: str | None = None,
    failure: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run one rollout action with the full gcloud transport stub and return command order."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = stub_commands(bin_dir)
    environment = rollout_environment(tmp_path, viewer_mode="configured")
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "GCP_LOG": str(log),
            "GCP_VM_JSON": vm_json(),
            "GCP_FIREWALL_JSON": firewall,
            "GCP_FIREWALL_AFTER_CREATE_JSON": after_create or correct_deny_rule(),
            "GCP_FIREWALL_LIST_JSON": json.dumps(rules or []),
            "GCP_CREATE_DONE": str(tmp_path / "created"),
            "GCP_FIREWALL_EXISTS": "1",
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
    if verification_state is not None:
        command.extend(("--verification-state", str(verification_state)))
    if failure is not None:
        environment["GCP_FAIL_COMMAND"] = failure
    result = subprocess.run(
        command,
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, log.read_text().splitlines()


def test_apply_bootstraps_staged_producer_before_first_installed_invocation(tmp_path: Path) -> None:
    """A clean VM receives a digest-checked producer before any remote action."""
    result, commands = apply_with_stub(tmp_path, firewall=correct_deny_rule())

    assert result.returncode == 0, result.stderr
    staged_copy = next(
        index
        for index, command in enumerate(commands)
        if "compute scp" in command and "viewer-auth-remote.sh" in command
    )
    bootstrap = next(index for index, command in enumerate(commands) if "sha256sum" in command)
    preflight = next(
        index
        for index, command in enumerate(commands)
        if "viewer-auth-remote.sh preflight" in command
    )
    assert staged_copy < bootstrap < preflight
    assert all("viewer-auth-remote.sh install-producer" not in command for command in commands)


def test_apply_replaces_named_rule_bound_to_the_wrong_vpc(tmp_path: Path) -> None:
    """A syntactically correct deny on another VPC cannot protect the target VM."""
    wrong_network = json.loads(correct_deny_rule())
    wrong_network["network"] = "https://www.googleapis.com/compute/v1/projects/rda-engineering/global/networks/wrong-vpc"
    result, commands = apply_with_stub(tmp_path, firewall=json.dumps(wrong_network))

    assert result.returncode == 0, result.stderr
    deleted = next(index for index, command in enumerate(commands) if "firewall-rules delete tasca-deny-public-8000" in command)
    created = next(index for index, command in enumerate(commands) if "firewall-rules create tasca-deny-public-8000" in command)
    assert deleted < created
    create_command = commands[created]
    assert f"--network={NETWORK}" in create_command
    assert "tcp:8000" in create_command
    assert "443" not in create_command


def test_apply_rejects_a_created_rule_that_does_not_validate_on_the_target_vpc(tmp_path: Path) -> None:
    """Creation succeeds only when its read-back rule matches the actual VM network contract."""
    malformed_after_create = json.loads(correct_deny_rule())
    malformed_after_create["sourceRanges"] = ["10.0.0.0/8"]
    wrong_network = json.loads(correct_deny_rule())
    wrong_network["network"] = "https://www.googleapis.com/compute/v1/projects/rda-engineering/global/networks/wrong-vpc"

    result, _commands = apply_with_stub(
        tmp_path,
        firewall=json.dumps(wrong_network),
        after_create=json.dumps(malformed_after_create),
    )

    assert result.returncode != 0
    assert "does not match the target VPC contract" in result.stderr


def test_apply_replaces_a_lower_precedence_deny_without_deleting_public_allow(tmp_path: Path) -> None:
    """Priority zero denies public TCP/8000 on the target VPC before any allow can match."""
    high_priority_allow = {
        "name": "allow-public-8000",
        "network": NETWORK,
        "direction": "INGRESS",
        "disabled": False,
        "priority": 800,
        "sourceRanges": ["0.0.0.0/0"],
        "targetTags": ["tasca-mcp"],
        "allowed": [{"IPProtocol": "tcp", "ports": ["7000-9000"]}],
    }
    legacy_deny = json.loads(correct_deny_rule())
    legacy_deny["priority"] = 900
    result, commands = apply_with_stub(
        tmp_path,
        firewall=json.dumps(legacy_deny),
        rules=[high_priority_allow],
    )

    assert result.returncode == 0, result.stderr
    deleted = next(
        index
        for index, command in enumerate(commands)
        if "firewall-rules delete tasca-deny-public-8000" in command
    )
    created = next(
        index
        for index, command in enumerate(commands)
        if "firewall-rules create tasca-deny-public-8000" in command
    )
    assert deleted < created
    create_command = commands[created]
    assert f"--network={NETWORK}" in create_command
    assert "--priority=0" in create_command
    assert "tcp:8000" in create_command
    assert not any("firewall-rules delete allow-public-8000" in command for command in commands)
    assert all("tcp:443" not in command for command in commands)


def test_reapply_marks_only_the_bounded_token_free_prepare_receipt(tmp_path: Path) -> None:
    """Rollout-owned reapply bridges prepare to cleanup without storing credentials."""
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

    result, commands = apply_with_stub(
        tmp_path,
        firewall=correct_deny_rule(),
        action="reapply",
        verification_state=receipt,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(receipt.read_text())
    assert state["phase"] == "reapplied"
    assert set(state) == {"format", "phase", "base_url", "expected_version", "table_id"}
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert any("viewer-auth-remote.sh apply" in command for command in commands)


def test_reapply_keeps_the_prepare_receipt_when_remote_restart_fails(tmp_path: Path) -> None:
    """Cleanup cannot read/delete the witness unless the rollout-owned restart succeeded."""
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

    result, commands = apply_with_stub(
        tmp_path,
        firewall=correct_deny_rule(),
        action="reapply",
        verification_state=receipt,
        failure="viewer-auth-remote.sh apply",
    )

    assert result.returncode != 0
    assert json.loads(receipt.read_text())["phase"] == "prepared"
    assert any("viewer-auth-remote.sh apply" in command for command in commands)
