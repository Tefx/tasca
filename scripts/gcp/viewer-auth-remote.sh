#!/usr/bin/env bash
# purpose: Activate or restore the SHA-verified Tasca release on the authorized single VM.
# usage: Installed by viewer-auth-rollout.sh and invoked as root with preflight, stage-tls, apply, rollback, or verify-public-read.
# effects: Captures/restores systemd and environment bytes, configures Caddy, and installs an exact wheel. It never formats, copies, migrates, deletes, or replaces SQLite bytes.
# requires: Debian VM with Caddy, uv, systemd, and the mounted /var/lib/tasca persistent disk. The optional TASCA_ROLLOUT_TEST_ROOT is only for offline fixture tests.
set -euo pipefail
umask 077

readonly RELEASE_VERSION="0.1.30"
readonly ROLLBACK_VERSION="0.1.29"
readonly ROOT_PREFIX="${TASCA_ROLLOUT_TEST_ROOT:-}"
readonly DATA_DIR="${ROOT_PREFIX}/var/lib/tasca"
readonly ENV_FILE="${ROOT_PREFIX}/etc/tasca/tasca.env"
readonly UNIT_FILE="${ROOT_PREFIX}/etc/systemd/system/tasca.service"
readonly BACKUP_DIR="${DATA_DIR}/rollback/${ROLLBACK_VERSION}"
readonly RELEASE_DIR="${ROOT_PREFIX}/opt/tasca/releases/${RELEASE_VERSION}"

fail() {
    printf 'tasca remote rollout: %s\n' "$*" >&2
    exit 1
}

require_root() {
    if [[ -n "$ROOT_PREFIX" ]]; then
        [[ "${TASCA_ROLLOUT_TESTING:-}" == "1" ]] || fail "test root requires TASCA_ROLLOUT_TESTING=1"
        return
    fi
    [[ "${EUID}" -eq 0 ]] || fail "must run as root"
}

require_version() {
    [[ "$1" == "$ROLLBACK_VERSION" ]] || fail "rollback version must be ${ROLLBACK_VERSION}"
}

validate_host() {
    [[ "$1" =~ ^[A-Za-z0-9.-]+$ ]] || fail "HTTPS host is invalid"
}

normalize_viewer_mode() {
    local raw="${1:-}"
    raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
    raw="${raw//[[:space:]]/}"
    case "$raw" in
        ""|public|clear|null|none) printf 'public\n' ;;
        configured) printf 'configured\n' ;;
        *) fail "viewer mode must be public or configured" ;;
    esac
}

state_path() {
    printf '%s/%s\n' "$BACKUP_DIR" "$1"
}

file_hash() {
    sha256sum -- "$1" | awk '{print $1}'
}

file_mode() {
    stat -c '%a' -- "$1"
}

file_identity() {
    stat -c '%d:%i:%s' -- "$1"
}

record_value() {
    printf '%s\n' "$2" > "$(state_path "$1")"
}

record_file() {
    local name="$1"
    local source="$2"
    cp --preserve=mode -- "$source" "$(state_path "$name")"
    record_value "${name}.sha256" "$(file_hash "$source")"
    record_value "${name}.mode" "$(file_mode "$source")"
}

assert_recorded_file() {
    local name="$1"
    local source="$2"
    cmp -- "$source" "$(state_path "$name")" || fail "${name} contents changed from captured state"
    [[ "$(file_hash "$source")" == "$(<"$(state_path "${name}.sha256")")" ]] \
        || fail "${name} hash changed from captured state"
    [[ "$(file_mode "$source")" == "$(<"$(state_path "${name}.mode")")" ]] \
        || fail "${name} mode changed from captured state"
}

normalized_db_path() {
    local path_count raw_line
    path_count="$(grep -c '^TASCA_DB_PATH=' "$ENV_FILE" || true)"
    [[ "$path_count" == "1" ]] || fail "TASCA_DB_PATH must appear exactly once"
    raw_line="$(grep '^TASCA_DB_PATH=' "$ENV_FILE")"

    local raw_path="${raw_line#TASCA_DB_PATH=}"
    [[ "$raw_path" != *[[:space:]]* ]] || fail "TASCA_DB_PATH must be normalized"
    [[ -n "$raw_path" && "$raw_path" == /* ]] || fail "TASCA_DB_PATH must be absolute"

    local candidate_path db_path data_path data_source db_source
    candidate_path="$raw_path"
    if [[ -n "$ROOT_PREFIX" ]]; then
        candidate_path="${ROOT_PREFIX}${raw_path}"
    fi
    db_path="$(realpath -e -- "$candidate_path")" || fail "TASCA_DB_PATH must exist"
    [[ -f "$db_path" ]] || fail "TASCA_DB_PATH must identify an existing database file"
    data_path="$(realpath -e -- "$DATA_DIR")" || fail "tasca-data mount is missing"
    [[ "$db_path" == "${data_path}/"* ]] || fail "TASCA_DB_PATH must be on tasca-data"
    data_source="$(findmnt -n -o SOURCE --target "$data_path")"
    db_source="$(findmnt -n -o SOURCE --target "$db_path")"
    [[ -n "$data_source" && "$data_source" == "$db_source" ]] \
        || fail "TASCA_DB_PATH must use the tasca-data filesystem"
    printf '%s\n' "$db_path"
}

capture_database_identity() {
    local db_path
    db_path="$(normalized_db_path)"
    record_value database.path "$db_path"
    record_value database.identity "$(file_identity "$db_path")"
    record_value database.sha256 "$(file_hash "$db_path")"
}

assert_database_identity() {
    local db_path
    db_path="$(normalized_db_path)"
    [[ "$db_path" == "$(<"$(state_path database.path)")" ]] \
        || fail "database path changed from captured state"
    [[ "$(file_identity "$db_path")" == "$(<"$(state_path database.identity)")" ]] \
        || fail "database device, inode, or size changed"
    [[ "$(file_hash "$db_path")" == "$(<"$(state_path database.sha256)")" ]] \
        || fail "database bytes changed"
}

runtime_version() {
    curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["version"])'
}

rollback_runtime_state() {
    local enabled active version
    enabled="$(systemctl is-enabled tasca.service 2>/dev/null || true)"
    active="$(systemctl is-active tasca.service 2>/dev/null || true)"
    [[ "$enabled" == "enabled" || "$enabled" == "disabled" ]] \
        || fail "service must have an enabled or disabled state"
    [[ "$active" == "active" ]] || fail "0.1.29 service must be active before release"
    version="$(runtime_version)"
    [[ "$version" == "$ROLLBACK_VERSION" ]] || fail "running service must be ${ROLLBACK_VERSION} before release"
    printf '%s\n%s\n%s\n' "$enabled" "$active" "$version"
}

initialize_and_read_rollback_database() {
    curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/tables \
        | python3 -c 'import json, sys; assert isinstance(json.load(sys.stdin), list)'
}

capture_service_state() {
    local state enabled active version
    state="$(rollback_runtime_state)"
    enabled="${state%%$'\n'*}"
    state="${state#*$'\n'}"
    active="${state%%$'\n'*}"
    version="${state#*$'\n'}"
    record_value service.enabled "$enabled"
    record_value service.active "$active"
    record_value service.version "$version"
}

assert_backup_integrity() {
    [[ -d "$BACKUP_DIR" ]] || fail "rollback inputs are missing"
    assert_recorded_file tasca.service "$(state_path tasca.service)"
    assert_recorded_file tasca.env "$(state_path tasca.env)"
    [[ "$(<"$(state_path service.enabled)")" == "enabled" || "$(<"$(state_path service.enabled)")" == "disabled" ]] \
        || fail "captured service enablement is invalid"
}

preflight() {
    local rollback_version=""
    while (($#)); do
        case "$1" in
            --rollback-version) rollback_version="${2:-}"; shift 2 ;;
            *) fail "unknown preflight argument: $1" ;;
        esac
    done
    require_version "$rollback_version"
    mountpoint -q "$DATA_DIR" || fail "persistent tasca-data disk is not mounted"
    [[ -f "$UNIT_FILE" ]] || fail "current service unit is missing"
    [[ -f "$ENV_FILE" ]] || fail "current service environment is missing"
    [[ ! -e "$BACKUP_DIR" ]] || fail "rollback inputs already exist; refusing to replace them"
    [[ "$(grep -c '^TASCA_VIEWER_TOKEN=' "$ENV_FILE" || true)" == "0" ]] \
        || fail "0.1.29 rollback environment must keep Viewer reads public"

    # Force the old runtime to initialize and read its database before capture.
    # This catches migration/WAL initialization before the identity receipt is recorded.
    rollback_runtime_state >/dev/null
    initialize_and_read_rollback_database
    normalized_db_path >/dev/null
    install -d -m 0700 "$BACKUP_DIR"
    record_file tasca.service "$UNIT_FILE"
    record_file tasca.env "$ENV_FILE"
    capture_database_identity
    capture_service_state
    printf 'tasca remote rollout: exact 0.1.29 runtime and rollback inputs captured\n'
}

stage_tls() {
    local host=""
    while (($#)); do
        case "$1" in
            --https-host) host="${2:-}"; shift 2 ;;
            *) fail "unknown stage-tls argument: $1" ;;
        esac
    done
    validate_host "$host"
    command -v caddy >/dev/null || fail "Caddy must be installed before TLS staging"
    install -d -m 0755 "${ROOT_PREFIX}/etc/caddy"
    cat > "${ROOT_PREFIX}/etc/caddy/Caddyfile" <<CADDY
${host} {
    reverse_proxy 127.0.0.1:8000
}
CADDY
    caddy validate --config "${ROOT_PREFIX}/etc/caddy/Caddyfile" --adapter caddyfile >/dev/null
    systemctl reload caddy || systemctl restart caddy
    ensure_tls "$host"
    printf 'tasca remote rollout: certificate-valid HTTPS is active\n'
}

ensure_tls() {
    local host="$1"
    validate_host "$host"
    curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
        --resolve "${host}:443:127.0.0.1" "https://${host}/api/v1/health" >/dev/null \
        || fail "HTTPS is not active; refusing credential access"
}

secret_to_file() {
    local secret_name="$1"
    local output_file="$2"
    local project_id access_token response
    project_id="$(curl --fail --silent --show-error -H 'Metadata-Flavor: Google' \
        http://metadata.google.internal/computeMetadata/v1/project/project-id)"
    access_token="$(curl --fail --silent --show-error -H 'Metadata-Flavor: Google' \
        http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')"
    response="$(curl --fail --silent --show-error -H "Authorization: Bearer ${access_token}" \
        "https://secretmanager.googleapis.com/v1/projects/${project_id}/secrets/${secret_name}/versions/latest:access")"
    printf '%s' "$response" | python3 -c \
        'import base64, json, sys; sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin.buffer)["payload"]["data"]))' \
        > "$output_file"
    unset access_token response
    [[ -s "$output_file" ]] || fail "secret retrieval returned an empty credential"
}

write_release_environment() {
    local temporary_dir="$1"
    local viewer_mode="$2"
    local base_environment="${temporary_dir}/base.env"
    local output_environment="${temporary_dir}/tasca.env"
    grep -Ev '^TASCA_(ADMIN_TOKEN|VIEWER_TOKEN|API_HOST|API_PORT)=' "$(state_path tasca.env)" \
        > "$base_environment" || true
    grep -q '^TASCA_DB_PATH=' "$base_environment" || fail "rollback environment lost database path"
    cat "$base_environment" > "$output_environment"
    printf 'TASCA_API_HOST=127.0.0.1\nTASCA_API_PORT=8000\nTASCA_ADMIN_TOKEN=' >> "$output_environment"
    cat "${temporary_dir}/admin" >> "$output_environment"
    if [[ "$viewer_mode" == "configured" ]]; then
        printf '\nTASCA_VIEWER_TOKEN=' >> "$output_environment"
        cat "${temporary_dir}/viewer" >> "$output_environment"
    fi
    printf '\n' >> "$output_environment"
    install -d -m 0700 "${ROOT_PREFIX}/etc/tasca"
    install -m 0640 "$output_environment" "$ENV_FILE"
}

write_release_unit() {
    cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=Tasca remote MCP service (0.1.30 exact wheel)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=tasca
Group=tasca
WorkingDirectory=${DATA_DIR}
EnvironmentFile=${ENV_FILE}
Environment=HOME=${DATA_DIR}
ExecStart=${RELEASE_DIR}/venv/bin/tasca
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT
}

apply_release() {
    local host=""
    local wheel=""
    local wheel_sha=""
    local release_version=""
    local viewer_mode=""
    while (($#)); do
        case "$1" in
            --https-host) host="${2:-}"; shift 2 ;;
            --wheel) wheel="${2:-}"; shift 2 ;;
            --wheel-sha) wheel_sha="${2:-}"; shift 2 ;;
            --release-version) release_version="${2:-}"; shift 2 ;;
            --viewer-mode) viewer_mode="${2:-}"; shift 2 ;;
            *) fail "unknown apply argument: $1" ;;
        esac
    done
    viewer_mode="$(normalize_viewer_mode "$viewer_mode")"
    validate_host "$host"
    [[ "$release_version" == "$RELEASE_VERSION" ]] || fail "release version must be ${RELEASE_VERSION}"
    [[ "$(basename -- "$wheel")" == "tasca-${RELEASE_VERSION}-py3-none-any.whl" ]] \
        || fail "exact 0.1.30 wheel is required"
    [[ -f "$wheel" && "$wheel_sha" =~ ^[[:xdigit:]]{64}$ ]] || fail "release wheel input is invalid"
    [[ "$(file_hash "$wheel")" == "$wheel_sha" ]] || fail "staged release wheel digest mismatch"
    assert_backup_integrity
    assert_database_identity
    ensure_tls "$host"

    local temporary_dir expected_viewer_auth
    temporary_dir="$(mktemp -d "${ROOT_PREFIX}/run/tasca-rollout.XXXXXX")"
    trap 'rm -rf "$temporary_dir"' EXIT
    secret_to_file tasca-admin-token "${temporary_dir}/admin"
    if [[ "$viewer_mode" == "configured" ]]; then
        secret_to_file tasca-viewer-token "${temporary_dir}/viewer"
        cmp -s "${temporary_dir}/admin" "${temporary_dir}/viewer" \
            && fail "configured credentials must differ"
        expected_viewer_auth=True
    else
        expected_viewer_auth=False
    fi

    command -v uv >/dev/null || fail "uv must be installed before release activation"
    install -d -m 0755 "$RELEASE_DIR"
    uv venv --clear --python python3 "${RELEASE_DIR}/venv"
    uv pip install --python "${RELEASE_DIR}/venv/bin/python" "$wheel"
    write_release_environment "$temporary_dir" "$viewer_mode"
    write_release_unit
    systemctl daemon-reload
    systemctl restart tasca.service
    curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health \
        | python3 -c "import json, sys; payload=json.load(sys.stdin); assert payload['version'] == '${RELEASE_VERSION}'; assert payload['viewer_auth_required'] is ${expected_viewer_auth}"
    assert_database_identity
    rm -rf "$temporary_dir"
    trap - EXIT
    printf 'tasca remote rollout: exact 0.1.30 wheel is active in %s Viewer mode\n' "$viewer_mode"
}

restore_enablement() {
    case "$(<"$(state_path service.enabled)")" in
        enabled) systemctl enable tasca.service ;;
        disabled) systemctl disable tasca.service ;;
        *) fail "captured service enablement is invalid" ;;
    esac
}

assert_rollback_restored() {
    assert_recorded_file tasca.service "$UNIT_FILE"
    assert_recorded_file tasca.env "$ENV_FILE"
    [[ "$(systemctl is-enabled tasca.service 2>/dev/null || true)" == "$(<"$(state_path service.enabled)")" ]] \
        || fail "service enablement changed from captured state"
    [[ "$(systemctl is-active tasca.service 2>/dev/null || true)" == "$(<"$(state_path service.active)")" ]] \
        || fail "service active state changed from captured state"
    [[ "$(runtime_version)" == "$(<"$(state_path service.version)")" ]] \
        || fail "runtime version changed from captured state"
}

rollback_release() {
    local rollback_version=""
    local host=""
    while (($#)); do
        case "$1" in
            --rollback-version) rollback_version="${2:-}"; shift 2 ;;
            --https-host) host="${2:-}"; shift 2 ;;
            *) fail "unknown rollback argument: $1" ;;
        esac
    done
    require_version "$rollback_version"
    validate_host "$host"
    assert_backup_integrity
    cp --preserve=mode -- "$(state_path tasca.env)" "$ENV_FILE"
    cp --preserve=mode -- "$(state_path tasca.service)" "$UNIT_FILE"
    systemctl daemon-reload
    restore_enablement
    systemctl restart tasca.service
    assert_rollback_restored
    verify_public_read --https-host "$host"
    # The public read can initialize SQLite/WAL state, so identity is final only after it.
    assert_database_identity
    printf 'tasca remote rollout: exact 0.1.29 public-read runtime restored\n'
}

verify_public_read() {
    local host=""
    while (($#)); do
        case "$1" in
            --https-host) host="${2:-}"; shift 2 ;;
            *) fail "unknown verify-public-read argument: $1" ;;
        esac
    done
    ensure_tls "$host"
    curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
        "https://${host}/api/v1/tables" >/dev/null
}

main() {
    require_root
    local action="${1:-}"
    [[ -n "$action" ]] || fail "missing action"
    shift
    case "$action" in
        preflight) preflight "$@" ;;
        stage-tls) stage_tls "$@" ;;
        apply) apply_release "$@" ;;
        rollback) rollback_release "$@" ;;
        verify-public-read) verify_public_read "$@" ;;
        *) fail "unknown action: $action" ;;
    esac
}

main "$@"
