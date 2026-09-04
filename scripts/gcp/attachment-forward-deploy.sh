#!/usr/bin/env bash
# purpose: Forward-deploy one caller-bound Tasca 0.1.32 wheel to the existing Tasca VM.
# usage: Set RELEASE_WHEEL and RELEASE_SHA256, then run render or apply. `apply` performs remote preflight before staging and activation.
# effects: `apply` transfers the exact wheel and switches only tasca.service's ExecStart. It preserves the service environment, SQLite/tasca-data, secrets, Caddy, firewall, loopback backend, and the 0.1.31 release.
# requires: A committed clean producer, gcloud access to rda-engineering/asia-southeast1-b/tasca-mcp, the admitted remote CPython 3.13, and a caller-provided wheel SHA-256. TASCA_FORWARD_TEST_ROOT is only for offline fixture tests.
set -euo pipefail
umask 077

readonly RELEASE_VERSION="0.1.32"
readonly PREVIOUS_VERSION="0.1.31"
readonly WHEEL_NAME="tasca-${RELEASE_VERSION}-py3-none-any.whl"
readonly PROJECT_ID="rda-engineering"
readonly ZONE="asia-southeast1-b"
readonly VM="tasca-mcp"
readonly HTTPS_HOST="34.1.134.239.sslip.io"
readonly REMOTE_PYTHON="/var/lib/tasca/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13"
readonly REMOTE_STAGE_DIR="/var/tmp/tasca-attachments-0.1.32"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
readonly ROOT_PREFIX="${TASCA_FORWARD_TEST_ROOT:-}"
readonly UNIT_FILE="${ROOT_PREFIX}/etc/systemd/system/tasca.service"
readonly PREVIOUS_RELEASE_DIR="${ROOT_PREFIX}/opt/tasca/releases/${PREVIOUS_VERSION}"
readonly RELEASE_DIR="${ROOT_PREFIX}/opt/tasca/releases/${RELEASE_VERSION}"
readonly UNIT_BACKUP="${RELEASE_DIR}/tasca.service.pre-${RELEASE_VERSION}"
readonly HEALTH_ATTEMPTS=10

fail() {
    printf 'attachment forward deploy: %s\n' "$*" >&2
    exit 1
}

file_hash() {
    sha256sum -- "$1" | awk '{print $1}'
}

require_root() {
    if [[ -n "$ROOT_PREFIX" ]]; then
        [[ "${TASCA_FORWARD_TESTING:-}" == "1" ]] || fail "test root requires TASCA_FORWARD_TESTING=1"
        return
    fi
    [[ "$EUID" -eq 0 ]] || fail "activate and preflight must run as root"
}

selected_python() {
    if [[ -n "$ROOT_PREFIX" ]]; then
        [[ -n "${TASCA_FORWARD_TEST_PYTHON:-}" ]] || fail "test root requires TASCA_FORWARD_TEST_PYTHON"
        printf '%s\n' "$TASCA_FORWARD_TEST_PYTHON"
        return
    fi
    printf '%s\n' "$REMOTE_PYTHON"
}

require_python() {
    local python="$1"
    local canonical identity
    [[ "$python" == /* && -x "$python" ]] || fail "selected Python must be an executable absolute path"
    canonical="$(realpath -e -- "$python")" || fail "selected Python cannot be resolved"
    [[ "$canonical" != "/usr/bin/python3" ]] || fail "selected Python must not be /usr/bin/python3"
    identity="$("$python" -c 'import sys; print(f"{sys.implementation.name}:{sys.version_info.major}.{sys.version_info.minor}")')" \
        || fail "selected Python cannot execute"
    [[ "$identity" == "cpython:3.13" ]] || fail "selected Python must be CPython 3.13"
}

require_release_inputs() {
    [[ -n "${RELEASE_WHEEL:-}" ]] || fail "RELEASE_WHEEL is required"
    [[ -n "${RELEASE_SHA256:-}" ]] || fail "RELEASE_SHA256 is required"
    [[ "$(basename -- "$RELEASE_WHEEL")" == "$WHEEL_NAME" ]] \
        || fail "RELEASE_WHEEL must be ${WHEEL_NAME}"
    [[ -f "$RELEASE_WHEEL" ]] || fail "RELEASE_WHEEL does not exist"
    [[ "$RELEASE_SHA256" =~ ^[[:xdigit:]]{64}$ ]] || fail "RELEASE_SHA256 must be a SHA-256 digest"
    [[ "$(file_hash "$RELEASE_WHEEL")" == "$RELEASE_SHA256" ]] \
        || fail "RELEASE_SHA256 does not match RELEASE_WHEEL"
}

require_committed_producer() {
    local file
    for file in scripts/gcp/attachment-forward-deploy.sh scripts/gcp/verify_attachments_remote.py; do
        git -C "$REPO_ROOT" ls-files --error-unmatch "$file" >/dev/null
    done
    git -C "$REPO_ROOT" diff --quiet -- \
        scripts/gcp/attachment-forward-deploy.sh scripts/gcp/verify_attachments_remote.py
    [[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all -- \
        scripts/gcp/attachment-forward-deploy.sh scripts/gcp/verify_attachments_remote.py)" ]] \
        || fail "forward producer and verifier must be committed and clean before a VM action"
}

emit_manifest() {
    python3 - "$WHEEL_NAME" "$RELEASE_SHA256" "$(git -C "$REPO_ROOT" rev-parse --verify HEAD)" \
        "$(file_hash "$SCRIPT_DIR/attachment-forward-deploy.sh")" \
        "$(file_hash "$SCRIPT_DIR/verify_attachments_remote.py")" <<'PY'
import json
import sys

wheel, digest, revision, producer_digest, verifier_digest = sys.argv[1:]
print(json.dumps({
    "release": {"version": "0.1.32", "wheel": wheel, "sha256": digest},
    "previous_release": "0.1.31",
    "target": {
        "project": "rda-engineering",
        "zone": "asia-southeast1-b",
        "vm": "tasca-mcp",
        "https_host": "34.1.134.239.sslip.io",
    },
    "producer": {
        "path": "scripts/gcp/attachment-forward-deploy.sh",
        "revision": revision,
        "sha256": producer_digest,
    },
    "verifier": {
        "path": "scripts/gcp/verify_attachments_remote.py",
        "expected_version": "0.1.32",
        "sha256": verifier_digest,
    },
    "python": {
        "path": "/var/lib/tasca/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13",
        "required": "CPython 3.13",
    },
    "actions": [
        "remote_read_only_preflight",
        "stage_tracked_producer_and_exact_wheel",
        "install_exact_wheel_in_fresh_0_1_32_venv",
        "atomically_switch_only_tasca_service_execstart",
        "bounded_local_and_https_health_readback",
        "restore_pre_0_1_32_unit_and_restart_0_1_31_on_activation_failure",
    ],
    "preserved": [
        "tasca.env", "Secret Manager values", "tasca-data SQLite", "Caddy", "firewall",
        "loopback backend", "/opt/tasca/releases/0.1.31",
    ],
}, sort_keys=True))
PY
}

remote_ssh() {
    gcloud compute ssh "$VM" --project="$PROJECT_ID" --zone="$ZONE" --quiet --command "$1"
}

stage_and_activate() {
    local producer_sha remote_command
    producer_sha="$(file_hash "$SCRIPT_DIR/attachment-forward-deploy.sh")"
    remote_ssh "install -d -m 0700 ${REMOTE_STAGE_DIR}"
    gcloud compute scp --project="$PROJECT_ID" --zone="$ZONE" --quiet \
        "$SCRIPT_DIR/attachment-forward-deploy.sh" \
        "${VM}:${REMOTE_STAGE_DIR}/attachment-forward-deploy.sh"
    remote_ssh "set -eu; actual=\$(sha256sum -- ${REMOTE_STAGE_DIR}/attachment-forward-deploy.sh | awk '{print \$1}'); test \"\$actual\" = \"${producer_sha}\"; sudo install -d -o root -g root -m 0755 /usr/local/lib/tasca; sudo install -o root -g root -m 0700 ${REMOTE_STAGE_DIR}/attachment-forward-deploy.sh /usr/local/lib/tasca/attachment-forward-deploy.sh; sudo /usr/local/lib/tasca/attachment-forward-deploy.sh preflight"
    gcloud compute scp --project="$PROJECT_ID" --zone="$ZONE" --quiet \
        "$RELEASE_WHEEL" "${VM}:${REMOTE_STAGE_DIR}/${WHEEL_NAME}"
    remote_command="sudo /usr/local/lib/tasca/attachment-forward-deploy.sh activate --wheel ${REMOTE_STAGE_DIR}/${WHEEL_NAME} --wheel-sha ${RELEASE_SHA256}"
    remote_ssh "$remote_command"
}

require_current_release() {
    [[ -f "$UNIT_FILE" ]] || fail "tasca.service is missing"
    [[ -x "${PREVIOUS_RELEASE_DIR}/venv/bin/tasca" ]] \
        || fail "${PREVIOUS_RELEASE_DIR}/venv/bin/tasca is missing"
    grep -Fx "ExecStart=${PREVIOUS_RELEASE_DIR}/venv/bin/tasca" "$UNIT_FILE" >/dev/null \
        || fail "tasca.service must still select the exact ${PREVIOUS_VERSION} release path"
    [[ "$(systemctl is-active tasca.service 2>/dev/null || true)" == "active" ]] \
        || fail "tasca.service must be active before the forward deployment"
    [[ ! -e "$RELEASE_DIR" ]] || fail "${RELEASE_DIR} already exists; reconcile before another activation"
}

verify_installed_version() {
    "${RELEASE_DIR}/venv/bin/python" -c \
        "from importlib.metadata import version; raise SystemExit(version('tasca') != '${RELEASE_VERSION}')" \
        || fail "exact ${RELEASE_VERSION} wheel did not install Tasca"
}

replace_unit_execstart() {
    local python="$1"
    "$python" - "$UNIT_FILE" "$UNIT_BACKUP" \
        "ExecStart=${PREVIOUS_RELEASE_DIR}/venv/bin/tasca" \
        "ExecStart=${RELEASE_DIR}/venv/bin/tasca" <<'PY'
import os
import stat
import sys
from pathlib import Path

unit = Path(sys.argv[1])
backup = Path(sys.argv[2])
previous = sys.argv[3]
current = sys.argv[4]
original = unit.read_text()
if backup.read_bytes() != original.encode():
    raise SystemExit("unit backup does not match the original unit")
if original.count(previous) != 1 or sum(line.startswith("ExecStart=") for line in original.splitlines()) != 1:
    raise SystemExit("unit must have exactly one previous-release ExecStart")
temporary = unit.with_name(f".{unit.name}.activate-{os.getpid()}")
temporary.write_text(original.replace(previous, current))
os.chmod(temporary, stat.S_IMODE(unit.stat().st_mode))
os.replace(temporary, unit)
PY
}

restore_unit() {
    local python="$1"
    "$python" - "$UNIT_FILE" "$UNIT_BACKUP" <<'PY'
import os
import stat
import sys
from pathlib import Path

unit = Path(sys.argv[1])
backup = Path(sys.argv[2])
if not backup.is_file():
    raise SystemExit("unit backup is missing")
temporary = unit.with_name(f".{unit.name}.restore-{os.getpid()}")
temporary.write_bytes(backup.read_bytes())
os.chmod(temporary, stat.S_IMODE(backup.stat().st_mode))
os.replace(temporary, unit)
PY
}

require_health() {
    local expected_version="$1"
    local url="$2"
    local label="$3"
    local attempt retry_delay=1
    [[ "${TASCA_FORWARD_TESTING:-}" == "1" ]] && retry_delay=0
    for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
        if curl --fail --silent --show-error --proto '=http,https' --tlsv1.2 "$url" \
            | python3 -c 'import json, sys; raise SystemExit(json.load(sys.stdin).get("version") != sys.argv[1])' "$expected_version"; then
            return
        fi
        if ((attempt < HEALTH_ATTEMPTS && retry_delay > 0)); then
            sleep "$retry_delay"
        fi
    done
    printf 'attachment forward deploy: %s health did not report %s within %s attempts\n' \
        "$label" "$expected_version" "$HEALTH_ATTEMPTS" >&2
    return 1
}

require_release_health() {
    local version="$1"
    require_health "$version" "http://127.0.0.1:8000/api/v1/health" "local"
    require_health "$version" "https://${HTTPS_HOST}/api/v1/health" "HTTPS"
}

restore_after_activation_failure() {
    local python="$1"
    restore_unit "$python" || fail "activation failed and the original unit could not be restored"
    systemctl daemon-reload || fail "activation failed and the restored unit could not be reloaded"
    systemctl restart tasca.service || fail "activation failed and the restored unit could not be restarted"
    require_release_health "$PREVIOUS_VERSION" \
        || fail "activation failed and restored ${PREVIOUS_VERSION} health could not be reconciled"
    printf 'attachment forward deploy: activation failed; pre-0.1.32 unit restored and 0.1.31 restarted\n' >&2
    exit 1
}

preflight() {
    local python
    require_root
    python="$(selected_python)"
    require_python "$python"
    require_current_release
    require_release_health "$PREVIOUS_VERSION"
    printf 'attachment forward deploy: preflight verified active %s and absent %s release path\n' \
        "$PREVIOUS_VERSION" "$RELEASE_VERSION"
}

activate() {
    local wheel=""
    local wheel_sha=""
    local python
    while (($#)); do
        case "$1" in
            --wheel) wheel="${2:-}"; shift 2 ;;
            --wheel-sha) wheel_sha="${2:-}"; shift 2 ;;
            *) fail "unknown activate argument: $1" ;;
        esac
    done
    require_root
    RELEASE_WHEEL="$wheel" RELEASE_SHA256="$wheel_sha" require_release_inputs
    python="$(selected_python)"
    require_python "$python"
    require_current_release

    install -d -o root -g root -m 0755 "$RELEASE_DIR"
    cp --preserve=mode -- "$UNIT_FILE" "$UNIT_BACKUP"
    chown root:root "$UNIT_BACKUP"
    uv venv --clear --python "$python" "${RELEASE_DIR}/venv"
    uv pip install --python "${RELEASE_DIR}/venv/bin/python" "$wheel"
    chown -R tasca:tasca "${RELEASE_DIR}/venv"
    verify_installed_version
    replace_unit_execstart "$python"

    if ! systemctl daemon-reload || ! systemctl restart tasca.service; then
        restore_after_activation_failure "$python"
    fi
    if ! require_release_health "$RELEASE_VERSION"; then
        restore_after_activation_failure "$python"
    fi
    printf 'attachment forward deploy: exact %s is active; %s remains available\n' \
        "$RELEASE_VERSION" "$PREVIOUS_RELEASE_DIR"
}

main() {
    local action="${1:-}"
    [[ -n "$action" ]] || fail "missing action"
    shift
    case "$action" in
        render)
            (($# == 0)) || fail "render accepts no arguments"
            require_release_inputs
            emit_manifest
            ;;
        apply)
            (($# == 0)) || fail "apply accepts no arguments"
            require_release_inputs
            require_committed_producer
            stage_and_activate
            ;;
        preflight)
            (($# == 0)) || fail "preflight accepts no arguments"
            preflight
            ;;
        activate)
            activate "$@"
            ;;
        *) fail "unknown action: $action" ;;
    esac
}

main "$@"
