#!/usr/bin/env bash
# purpose: Reconcile or activate the tracked viewer-auth release on the existing GCE VM.
# usage: Set the exact release, Python, and rollback-bundle inputs, then run apply, resume, reapply, rollback, or render.
# effects: Transfers committed producer/artifact bytes and, only for a selected runtime action, changes the named existing VM and Admin Secret version. It never publishes a package or changes SQLite bytes.
# requires: gcloud, sha256sum, git, Python 3 for local bundle verification, a committed clean scripts/gcp producer, and the authorized rda-engineering target.
set -euo pipefail

readonly RELEASE_VERSION="0.1.30"
readonly ROLLBACK_VERSION="0.1.29"
readonly WHEEL_NAME="tasca-${RELEASE_VERSION}-py3-none-any.whl"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
readonly REMOTE_PRODUCER="$SCRIPT_DIR/viewer-auth-remote.sh"
readonly REMOTE_VERIFIER="$SCRIPT_DIR/verify_viewer_auth_remote.py"
readonly BUNDLE_BUILDER="$SCRIPT_DIR/build-viewer-auth-rollback-bundle.sh"
readonly REMOTE_STAGE_DIR="/var/tmp/tasca-viewer-auth-rollout"
readonly FIREWALL_RULE="tasca-deny-public-8000"
readonly FIREWALL_DENY_PRIORITY=0

usage() {
    cat <<'USAGE'
Usage:
  viewer-auth-rollout.sh apply --require-version 0.1.30 --rollback-version 0.1.29 [--rehearse-rollback]
  viewer-auth-rollout.sh resume --require-version 0.1.30 --rollback-version 0.1.29 --rehearse-rollback [--rotate-admin-secret-version]
  viewer-auth-rollout.sh reapply --require-version 0.1.30 --rollback-version 0.1.29 --verification-state <0600-token-free-state-file>
  viewer-auth-rollout.sh rollback --rollback-version 0.1.29
  viewer-auth-rollout.sh render --require-version 0.1.30 --rollback-version 0.1.29 [--rehearse-rollback]

Required environment for all actions:
  PROJECT_ID, ZONE, VM, TASCA_HTTPS_HOST, TASCA_PYTHON,
  TASCA_ROLLBACK_BUNDLE, TASCA_ROLLBACK_SHA256

apply, reapply, resume, and render also require:
  RELEASE_WHEEL, RELEASE_SHA256

TASCA_PYTHON is an absolute remote CPython 3.13 path. The remote producer
rejects /usr/bin/python3 and incompatible paths before TLS, credential, or
service effects. TASCA_VIEWER_MODE is optional: absent, public, clear, null,
and none select public Viewer reads; configured fetches Viewer only after TLS.
USAGE
}

die() {
    printf 'viewer-auth rollout: %s\n' "$*" >&2
    exit 1
}

normalize_viewer_mode() {
    local raw="${TASCA_VIEWER_MODE:-}"
    raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
    raw="${raw//[[:space:]]/}"
    case "$raw" in
        ""|public|clear|null|none) printf 'public\n' ;;
        configured) printf 'configured\n' ;;
        *) die "TASCA_VIEWER_MODE must be public or configured" ;;
    esac
}

require_environment() {
    local name
    for name in PROJECT_ID ZONE VM TASCA_HTTPS_HOST TASCA_PYTHON TASCA_ROLLBACK_BUNDLE TASCA_ROLLBACK_SHA256; do
        [[ -n "${!name:-}" ]] || die "missing required environment: ${name}"
    done
    [[ "$PROJECT_ID" == "rda-engineering" ]] || die "PROJECT_ID must be rda-engineering"
    [[ "$VM" == "tasca-mcp" ]] || die "VM must be tasca-mcp"
    [[ "$ZONE" == "asia-southeast1-b" ]] || die "ZONE must be asia-southeast1-b"
    [[ "$TASCA_HTTPS_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || die "TASCA_HTTPS_HOST is invalid"
    [[ "$TASCA_PYTHON" == /* && "$TASCA_PYTHON" != "/usr/bin/python3" ]] \
        || die "TASCA_PYTHON must select a non-system absolute interpreter path"
}

require_release_inputs() {
    [[ -n "${RELEASE_WHEEL:-}" ]] || die "missing required environment: RELEASE_WHEEL"
    [[ -n "${RELEASE_SHA256:-}" ]] || die "missing required environment: RELEASE_SHA256"
    [[ "$(basename -- "$RELEASE_WHEEL")" == "$WHEEL_NAME" ]] || die "release wheel filename must be ${WHEEL_NAME}"
    [[ -f "$RELEASE_WHEEL" ]] || die "release wheel does not exist"
    [[ "$RELEASE_SHA256" =~ ^[[:xdigit:]]{64}$ ]] || die "RELEASE_SHA256 must be a SHA-256 digest"
    [[ "$(sha256sum -- "$RELEASE_WHEEL" | awk '{print $1}')" == "$RELEASE_SHA256" ]] \
        || die "RELEASE_SHA256 does not match the release wheel"
}

rollback_bundle_name() {
    basename -- "$TASCA_ROLLBACK_BUNDLE"
}

require_rollback_inputs() {
    [[ -f "$TASCA_ROLLBACK_BUNDLE" ]] || die "TASCA_ROLLBACK_BUNDLE does not exist"
    [[ "$TASCA_ROLLBACK_SHA256" =~ ^[[:xdigit:]]{64}$ ]] \
        || die "TASCA_ROLLBACK_SHA256 must be a SHA-256 digest"
    [[ "$(sha256sum -- "$TASCA_ROLLBACK_BUNDLE" | awk '{print $1}')" == "$TASCA_ROLLBACK_SHA256" ]] \
        || die "TASCA_ROLLBACK_SHA256 does not match the rollback bundle"
    bash "$BUNDLE_BUILDER" verify --bundle "$TASCA_ROLLBACK_BUNDLE" \
        --sha256 "$TASCA_ROLLBACK_SHA256" >/dev/null \
        || die "rollback bundle does not bind the current tracked producer"
}

producer_revision() {
    git -C "$REPO_ROOT" rev-parse --verify HEAD
}

producer_sha256() {
    sha256sum -- "$REMOTE_PRODUCER" | awk '{print $1}'
}

verifier_sha256() {
    sha256sum -- "$REMOTE_VERIFIER" | awk '{print $1}'
}

bundle_builder_sha256() {
    sha256sum -- "$BUNDLE_BUILDER" | awk '{print $1}'
}

require_committed_producer() {
    local file
    for file in \
        scripts/gcp/viewer-auth-rollout.sh \
        scripts/gcp/viewer-auth-remote.sh \
        scripts/gcp/build-viewer-auth-rollback-bundle.sh \
        scripts/gcp/verify_viewer_auth_remote.py; do
        git -C "$REPO_ROOT" ls-files --error-unmatch "$file" >/dev/null
    done
    git -C "$REPO_ROOT" diff --quiet -- scripts/gcp
    [[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all -- scripts/gcp)" ]] \
        || die "scripts/gcp must be committed and clean before a VM action"
}

emit_manifest() {
    local rehearse="$1"
    local viewer_mode="$2"
    python3 - "$WHEEL_NAME" "$RELEASE_SHA256" "$(producer_revision)" "$(producer_sha256)" \
        "$(verifier_sha256)" "$(bundle_builder_sha256)" "$TASCA_ROLLBACK_SHA256" \
        "$(rollback_bundle_name)" "$rehearse" "$viewer_mode" <<'PY'
import json
import sys

(
    wheel,
    digest,
    revision,
    remote_digest,
    verifier_digest,
    builder_digest,
    rollback_digest,
    rollback_bundle,
    rehearse,
    viewer_mode,
) = sys.argv[1:]
actions = [
    "verify_tracked_rollback_bundle",
    "transfer_tracked_remote_producer",
    "verify_and_install_staged_producer",
    "transfer_exact_wheel_and_rollback_bundle",
    "preflight_explicit_cpython_3_13_and_rollback_capture",
    "stage_certificate_valid_https_without_backend_health",
    "resolve_vm_vpc_and_reconcile_effective_public_tcp_8000",
]
if viewer_mode == "configured":
    actions.append("fetch_redacted_viewer_secret_after_tls")
actions.append("install_exact_0_1_30_wheel")
if rehearse == "true":
    actions.extend(("install_offline_0_1_29_bundle", "restore_0_1_29_public_read", "reinstall_exact_0_1_30_wheel"))
print(json.dumps({
    "release": {"version": "0.1.30", "wheel": wheel, "sha256": digest},
    "rollback": {"version": "0.1.29", "bundle": rollback_bundle, "sha256": rollback_digest},
    "producer": {
        "revision": revision,
        "remote_sha256": remote_digest,
        "rollback_builder_sha256": builder_digest,
    },
    "verifier": {
        "path": "scripts/gcp/verify_viewer_auth_remote.py",
        "revision": revision,
        "sha256": verifier_digest,
    },
    "actions": actions,
    "viewer": {"mode": viewer_mode, "credential": "redacted" if viewer_mode == "configured" else "absent"},
    "python": {"selector": "TASCA_PYTHON", "required": "CPython 3.13"},
    "backend": {"bind": "127.0.0.1:8000", "writers": 1},
    "database": {"device": "tasca-data", "sqlite_bytes": "identity-checked"},
    "persistence": {"protocol": "prepare-rollout-reapply-cleanup", "state": "0600-token-free"},
}, sort_keys=True))
PY
}

remote_command() {
    local command="sudo /usr/local/lib/tasca/viewer-auth-remote.sh"
    local argument quoted
    for argument in "$@"; do
        printf -v quoted '%q' "$argument"
        command+=" ${quoted}"
    done
    gcloud compute ssh "$VM" --project="$PROJECT_ID" --zone="$ZONE" --quiet --command "$command"
}

ensure_remote_stage() {
    gcloud compute ssh "$VM" --project="$PROJECT_ID" --zone="$ZONE" --quiet \
        --command "install -d -m 0700 ${REMOTE_STAGE_DIR}"
}

bootstrap_staged_producer() {
    local expected_sha="$1"
    local source="${REMOTE_STAGE_DIR}/viewer-auth-remote.sh"
    local source_q expected_q command
    printf -v source_q '%q' "$source"
    printf -v expected_q '%q' "$expected_sha"
    command="set -eu; actual=\$(sha256sum -- ${source_q} | awk '{print \$1}'); test \"\$actual\" = ${expected_q}; sudo install -d -o root -g root -m 0755 /usr/local/lib/tasca; sudo install -o root -g root -m 0700 ${source_q} /usr/local/lib/tasca/viewer-auth-remote.sh"
    gcloud compute ssh "$VM" --project="$PROJECT_ID" --zone="$ZONE" --quiet --command "$command"
}

stage_tracked_inputs() {
    local producer_digest
    producer_digest="$(producer_sha256)"
    ensure_remote_stage
    gcloud compute scp --project="$PROJECT_ID" --zone="$ZONE" --quiet \
        "$REMOTE_PRODUCER" "${VM}:${REMOTE_STAGE_DIR}/viewer-auth-remote.sh"
    bootstrap_staged_producer "$producer_digest"
    if [[ -n "${RELEASE_WHEEL:-}" ]]; then
        gcloud compute scp --project="$PROJECT_ID" --zone="$ZONE" --quiet \
            "$RELEASE_WHEEL" "${VM}:${REMOTE_STAGE_DIR}/${WHEEL_NAME}"
    fi
    gcloud compute scp --project="$PROJECT_ID" --zone="$ZONE" --quiet \
        "$TASCA_ROLLBACK_BUNDLE" "${VM}:${REMOTE_STAGE_DIR}/$(rollback_bundle_name)"
}

vm_network_and_tags() {
    gcloud compute instances describe "$VM" --project="$PROJECT_ID" --zone="$ZONE" --format=json --quiet \
        | python3 -c '
import json
import sys
instance = json.load(sys.stdin)
interfaces = instance.get("networkInterfaces")
if not isinstance(interfaces, list) or not interfaces:
    raise SystemExit("VM does not expose a VPC network")
network = interfaces[0].get("network")
if not isinstance(network, str) or "/global/networks/" not in network:
    raise SystemExit("VM network is invalid")
tags = instance.get("tags", {}).get("items", [])
if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
    raise SystemExit("VM tags are invalid")
print(network)
print(json.dumps(tags, separators=(",", ":")))
'
}

firewall_rule_is_correct() {
    local expected_network="$1"
    python3 -c '
import json
import sys
try:
    rule = json.load(sys.stdin)
except json.JSONDecodeError:
    raise SystemExit(1)
expected_network = sys.argv[1]
denied = rule.get("denied", [])
has_tcp_8000 = any(
    entry.get("IPProtocol") == "tcp" and "8000" in entry.get("ports", [])
    for entry in denied
    if isinstance(entry, dict)
)
correct = (
    rule.get("network") == expected_network
    and rule.get("direction") == "INGRESS"
    and rule.get("disabled") is False
    and rule.get("priority") == 0
    and rule.get("sourceRanges") == ["0.0.0.0/0"]
    and rule.get("targetTags") == ["tasca-mcp"]
    and has_tcp_8000
)
raise SystemExit(0 if correct else 1)
' "$expected_network"
}

create_public_tcp_8000_deny() {
    local network="$1"
    gcloud compute firewall-rules create "$FIREWALL_RULE" --project="$PROJECT_ID" --network="$network" \
        --direction=INGRESS --priority="$FIREWALL_DENY_PRIORITY" --action=DENY --rules=tcp:8000 \
        --source-ranges=0.0.0.0/0 --target-tags=tasca-mcp --quiet
}

validate_named_public_tcp_8000_deny() {
    local network="$1"
    local rule_json
    rule_json="$(gcloud compute firewall-rules describe "$FIREWALL_RULE" \
        --project="$PROJECT_ID" --format=json --quiet)" \
        || die "created TCP/8000 deny rule could not be read back"
    printf '%s' "$rule_json" | firewall_rule_is_correct "$network" \
        || die "created TCP/8000 deny rule does not match the target VPC contract"
}

reconcile_public_backend_port() {
    local context network tags_json rule_json
    gcloud compute instances add-tags "$VM" --project="$PROJECT_ID" --zone="$ZONE" \
        --tags=tasca-mcp --quiet
    context="$(vm_network_and_tags)" || die "could not resolve the target VM VPC network"
    network="${context%%$'\n'*}"
    tags_json="${context#*$'\n'}"
    [[ "$network" == */global/networks/* && "$tags_json" != "$context" ]] \
        || die "target VM VPC network or tags are invalid"
    python3 -c 'import json, sys; raise SystemExit(0 if "tasca-mcp" in json.loads(sys.argv[1]) else 1)' \
        "$tags_json" || die "target VM is missing the tasca-mcp firewall tag"

    if rule_json="$(gcloud compute firewall-rules describe "$FIREWALL_RULE" \
        --project="$PROJECT_ID" --format=json --quiet 2>/dev/null)"; then
        if printf '%s' "$rule_json" | firewall_rule_is_correct "$network"; then
            return
        fi
        gcloud compute firewall-rules delete "$FIREWALL_RULE" --project="$PROJECT_ID" --quiet
    fi
    create_public_tcp_8000_deny "$network"
    validate_named_public_tcp_8000_deny "$network"
}

assert_resume_effects() {
    local context network tags_json rule_json viewer_enabled viewer_line
    local -a viewer_versions=()
    context="$(vm_network_and_tags)" || die "could not reconcile the target VM VPC network"
    network="${context%%$'\n'*}"
    tags_json="${context#*$'\n'}"
    [[ "$network" == */global/networks/* && "$tags_json" != "$context" ]] \
        || die "target VM VPC network or tags are invalid"
    python3 -c 'import json, sys; raise SystemExit(0 if "tasca-mcp" in json.loads(sys.argv[1]) else 1)' \
        "$tags_json" || die "resume refuses a VM without the tasca-mcp firewall tag"
    rule_json="$(gcloud compute firewall-rules describe "$FIREWALL_RULE" \
        --project="$PROJECT_ID" --format=json --quiet)" \
        || die "resume refuses a missing TCP/8000 deny rule"
    printf '%s' "$rule_json" | firewall_rule_is_correct "$network" \
        || die "resume refuses a mismatched TCP/8000 deny rule"
    viewer_enabled="$(gcloud secrets versions list tasca-viewer-token --project="$PROJECT_ID" \
        --filter='state=ENABLED' --format='value(name.basename())' --quiet)"
    [[ -n "$viewer_enabled" ]] || die "resume requires exactly enabled Viewer secret version 1"
    while IFS= read -r viewer_line; do
        [[ "$viewer_line" =~ ^[1-9][0-9]*$ ]] \
            || die "resume found an invalid enabled Viewer secret version"
        viewer_versions+=("$viewer_line")
    done <<< "$viewer_enabled"
    [[ "${#viewer_versions[@]}" == "1" && "${viewer_versions[0]}" == "1" ]] \
        || die "resume requires exactly enabled Viewer secret version 1"
}

mark_verification_reapplied() {
    local state_file="$1"
    [[ -f "$state_file" ]] || die "verification state file is missing"
    python3 - "$state_file" "$RELEASE_VERSION" <<'PY'
import json
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
version = sys.argv[2]
if path.stat().st_size > 4096:
    raise SystemExit("verification state file exceeds 4096 bytes")
state = json.loads(path.read_text())
expected = {"format", "phase", "base_url", "expected_version", "table_id"}
if set(state) != expected or state.get("format") != "tasca-viewer-auth-persistence-v1":
    raise SystemExit("verification state file has an invalid schema")
if state.get("phase") != "prepared" or state.get("expected_version") != version:
    raise SystemExit("verification state file is not a prepared 0.1.30 receipt")
if not isinstance(state.get("base_url"), str) or not isinstance(state.get("table_id"), str):
    raise SystemExit("verification state file has invalid values")
state["phase"] = "reapplied"
temporary = path.with_name(f".{path.name}.tmp")
temporary.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
os.replace(temporary, path)
os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
PY
}

remote_release_command() {
    local action="$1"
    local viewer_mode="$2"
    remote_command "$action" --https-host "$TASCA_HTTPS_HOST" \
        --wheel "${REMOTE_STAGE_DIR}/${WHEEL_NAME}" --wheel-sha "$RELEASE_SHA256" \
        --release-version "$RELEASE_VERSION" --viewer-mode "$viewer_mode" \
        --python "$TASCA_PYTHON" --rollback-bundle "${REMOTE_STAGE_DIR}/$(rollback_bundle_name)" \
        --rollback-sha "$TASCA_ROLLBACK_SHA256"
}

activate_release() {
    local viewer_mode="$1"
    remote_command stage-tls --https-host "$TASCA_HTTPS_HOST"
    reconcile_public_backend_port
    remote_release_command apply "$viewer_mode"
}

apply_release() {
    local rehearse="$1"
    local viewer_mode="$2"
    require_committed_producer
    stage_tracked_inputs
    remote_command preflight --rollback-version "$ROLLBACK_VERSION" --python "$TASCA_PYTHON" \
        --rollback-bundle "${REMOTE_STAGE_DIR}/$(rollback_bundle_name)" --rollback-sha "$TASCA_ROLLBACK_SHA256"
    activate_release "$viewer_mode"
    if [[ "$rehearse" == "true" ]]; then
        remote_release_command rehearse "$viewer_mode"
    fi
    printf 'viewer-auth rollout: exact %s staged with tracked producer %s\n' \
        "$RELEASE_VERSION" "$(producer_revision)"
}

reapply_release() {
    local viewer_mode="$1"
    local verification_state="$2"
    require_committed_producer
    stage_tracked_inputs
    activate_release "$viewer_mode"
    mark_verification_reapplied "$verification_state"
    printf 'viewer-auth rollout: exact %s re-applied and persistence receipt marked\n' "$RELEASE_VERSION"
}

ROTATED_ADMIN_OLD_VERSION=""
rotate_admin_secret_version() {
    local enabled new_version line version old_count post_version
    local -a versions=()
    enabled="$(gcloud secrets versions list tasca-admin-token --project="$PROJECT_ID" \
        --filter='state=ENABLED' --format='value(name.basename())' --quiet)"
    while IFS= read -r line; do
        [[ "$line" =~ ^[1-9][0-9]*$ ]] \
            || die "Admin rotation found an invalid enabled version"
        versions+=("$line")
    done <<< "$enabled"
    case "${#versions[@]}" in
        1)
            version="${versions[0]}"
            if [[ "$version" == "1" ]]; then
                new_version="$(head -c 48 /dev/urandom | base64 | tr -d '\n' \
                    | gcloud secrets versions add tasca-admin-token --project="$PROJECT_ID" --data-file=- \
                        --format='value(name.basename())' --quiet)"
                [[ "$new_version" =~ ^([2-9]|[1-9][0-9]+)$ ]] \
                    || die "Admin rotation did not create a new named secret version"
                ROTATED_ADMIN_OLD_VERSION="1"
                return
            fi
            [[ "$version" =~ ^([2-9]|[1-9][0-9]+)$ ]] \
                || die "Admin rotation found an invalid enabled version"
            printf 'viewer-auth rollout: Admin rotation is already finalized at enabled version %s\n' "$version"
            ;;
        2)
            old_count=0
            post_version=""
            for version in "${versions[@]}"; do
                if [[ "$version" == "1" ]]; then
                    ((old_count += 1))
                elif [[ "$version" =~ ^([2-9]|[1-9][0-9]+)$ ]]; then
                    post_version="$version"
                else
                    die "Admin rotation found an invalid post-version-1 state"
                fi
            done
            [[ "$old_count" == "1" && -n "$post_version" ]] \
                || die "Admin rotation found ambiguous enabled versions"
            ROTATED_ADMIN_OLD_VERSION="1"
            printf 'viewer-auth rollout: reusing the existing post-version-1 Admin secret\n'
            ;;
        *) die "Admin rotation found ambiguous enabled versions" ;;
    esac
}

disable_rotated_admin_version() {
    [[ -n "$ROTATED_ADMIN_OLD_VERSION" ]] || return 0
    gcloud secrets versions disable "$ROTATED_ADMIN_OLD_VERSION" --project="$PROJECT_ID" \
        --secret=tasca-admin-token --quiet
}

resume_release() {
    local rehearse="$1"
    local rotate_admin="$2"
    local viewer_mode="$3"
    require_committed_producer
    assert_resume_effects
    stage_tracked_inputs
    if remote_command verify-public-read --https-host "$TASCA_HTTPS_HOST" --python "$TASCA_PYTHON" \
        >/dev/null 2>&1; then
        [[ "$rehearse" == "true" ]] \
            || die "recovery from exact ${ROLLBACK_VERSION} public-read state requires --rehearse-rollback"
        [[ "$rotate_admin" == "true" ]] \
            || die "recovery from exact ${ROLLBACK_VERSION} public-read state requires --rotate-admin-secret-version"
        rotate_admin_secret_version
        remote_release_command apply "$viewer_mode"
        disable_rotated_admin_version
        printf 'viewer-auth rollout: exact %s public-read state recovered without rollback replay\n' \
            "$ROLLBACK_VERSION"
        return
    fi
    remote_release_command reconcile "$viewer_mode"
    if [[ "$rotate_admin" == "true" ]]; then
        [[ "$rehearse" == "true" ]] || die "Admin rotation requires --rehearse-rollback"
        rotate_admin_secret_version
    fi
    if [[ "$rehearse" == "true" ]]; then
        remote_release_command rehearse "$viewer_mode"
    fi
    disable_rotated_admin_version
    printf 'viewer-auth rollout: existing %s state reconciled without TLS, firewall, or Viewer-secret replay\n' \
        "$RELEASE_VERSION"
}

rollback_release() {
    require_committed_producer
    stage_tracked_inputs
    remote_command rollback --rollback-version "$ROLLBACK_VERSION" --https-host "$TASCA_HTTPS_HOST" \
        --python "$TASCA_PYTHON" --rollback-bundle "${REMOTE_STAGE_DIR}/$(rollback_bundle_name)" \
        --rollback-sha "$TASCA_ROLLBACK_SHA256"
    printf 'viewer-auth rollout: %s public-read rollback restored from immutable bundle\n' "$ROLLBACK_VERSION"
}

main() {
    local action="${1:-}"
    [[ -n "$action" ]] || { usage >&2; exit 2; }
    shift
    local required_version=""
    local rollback_version=""
    local rehearse="false"
    local verification_state=""
    local rotate_admin="false"
    while (($#)); do
        case "$1" in
            --require-version) required_version="${2:-}"; shift 2 ;;
            --rollback-version) rollback_version="${2:-}"; shift 2 ;;
            --rehearse-rollback) rehearse="true"; shift ;;
            --verification-state) verification_state="${2:-}"; shift 2 ;;
            --rotate-admin-secret-version) rotate_admin="true"; shift ;;
            --help|-h) usage; return 0 ;;
            *) die "unknown argument: $1" ;;
        esac
    done

    require_environment
    require_rollback_inputs
    local viewer_mode
    viewer_mode="$(normalize_viewer_mode)"
    [[ "$rollback_version" == "$ROLLBACK_VERSION" ]] || die "rollback version must be ${ROLLBACK_VERSION}"
    case "$action" in
        apply|render|reapply|resume)
            [[ "$required_version" == "$RELEASE_VERSION" ]] || die "required version must be ${RELEASE_VERSION}"
            require_release_inputs
            ;;
        rollback) ;;
        *) usage >&2; exit 2 ;;
    esac

    case "$action" in
        render)
            [[ -z "$verification_state" && "$rotate_admin" == "false" ]] \
                || die "render accepts no reapply or rotation options"
            emit_manifest "$rehearse" "$viewer_mode"
            ;;
        apply)
            [[ -z "$verification_state" && "$rotate_admin" == "false" ]] \
                || die "apply accepts no reapply or rotation options"
            apply_release "$rehearse" "$viewer_mode"
            ;;
        reapply)
            [[ "$rehearse" == "false" && -n "$verification_state" && "$rotate_admin" == "false" ]] \
                || die "reapply requires --verification-state and accepts no rollout rehearsal or rotation"
            reapply_release "$viewer_mode" "$verification_state"
            ;;
        resume)
            [[ -z "$verification_state" ]] || die "resume does not accept --verification-state"
            resume_release "$rehearse" "$rotate_admin" "$viewer_mode"
            ;;
        rollback)
            [[ "$rehearse" == "false" && -z "$verification_state" && "$rotate_admin" == "false" ]] \
                || die "rollback accepts no reapply, rehearsal, or rotation options"
            rollback_release
            ;;
    esac
}

main "$@"
