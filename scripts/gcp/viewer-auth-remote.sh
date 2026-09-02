#!/usr/bin/env bash
# purpose: Reconcile, activate, rehearse, or restore the SHA-verified Tasca release on the authorized VM.
# usage: Installed by viewer-auth-rollout.sh and invoked as root with preflight, stage-tls, apply, reconcile, rehearse, rollback, reseal-sqlite-logical-state, or verify-public-read.
# effects: Captures/restores systemd and environment state, configures Caddy, and installs exact wheel bytes. It never formats, copies, migrates, deletes, or replaces SQLite bytes.
# requires: Debian VM with Caddy, uv, systemd, an admitted CPython 3.13 path, and mounted /var/lib/tasca persistent disk. TASCA_ROLLOUT_TEST_ROOT is only for offline fixture tests.
set -euo pipefail
umask 077

readonly RELEASE_VERSION="0.1.30"
readonly ROLLBACK_VERSION="0.1.29"
readonly HEALTH_ATTEMPTS=30
readonly ROOT_PREFIX="${TASCA_ROLLOUT_TEST_ROOT:-}"
readonly DATA_DIR="${ROOT_PREFIX}/var/lib/tasca"
readonly ENV_FILE="${ROOT_PREFIX}/etc/tasca/tasca.env"
readonly UNIT_FILE="${ROOT_PREFIX}/etc/systemd/system/tasca.service"
readonly BACKUP_DIR="${DATA_DIR}/rollback/${ROLLBACK_VERSION}"
readonly RELEASE_DIR="${ROOT_PREFIX}/opt/tasca/releases/${RELEASE_VERSION}"
readonly ROLLBACK_RELEASE_DIR="${ROOT_PREFIX}/opt/tasca/releases/${ROLLBACK_VERSION}"

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

file_device_inode() {
    stat -c '%d:%i' -- "$1"
}

database_disk_source() {
    findmnt -n -o SOURCE --target "$1"
}

require_python() {
    local python="$1"
    local canonical identity
    [[ "$python" == /* && -x "$python" ]] || fail "TASCA_PYTHON must be an executable absolute path"
    canonical="$(readlink -f -- "$python")" || fail "TASCA_PYTHON cannot be resolved"
    [[ "$canonical" != "/usr/bin/python3" ]] || fail "TASCA_PYTHON must not select /usr/bin/python3"
    identity="$("$python" -c 'import sys; print(f"{sys.implementation.name}:{sys.version_info.major}.{sys.version_info.minor}")')" \
        || fail "TASCA_PYTHON cannot execute"
    [[ "$identity" == "cpython:3.13" ]] \
        || fail "TASCA_PYTHON must select CPython 3.13 before rollout effects"
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
    local path_count raw_line raw_path candidate_path db_path data_path data_source db_source
    path_count="$(grep -c '^TASCA_DB_PATH=' "$ENV_FILE" || true)"
    [[ "$path_count" == "1" ]] || fail "TASCA_DB_PATH must appear exactly once"
    raw_line="$(grep '^TASCA_DB_PATH=' "$ENV_FILE")"
    raw_path="${raw_line#TASCA_DB_PATH=}"
    [[ "$raw_path" != *[[:space:]]* ]] || fail "TASCA_DB_PATH must be normalized"
    [[ -n "$raw_path" && "$raw_path" == /* ]] || fail "TASCA_DB_PATH must be absolute"
    candidate_path="$raw_path"
    [[ -z "$ROOT_PREFIX" ]] || candidate_path="${ROOT_PREFIX}${raw_path}"
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

database_logical_state() {
    local python="$1"
    local db_path="$2"
    "$python" - "$db_path" <<'PY'
import json
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
connection: sqlite3.Connection | None = None
try:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise SystemExit("database integrity check failed")
    schema = [
        {"name": name, "type": kind}
        for kind, name in connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('index', 'table', 'trigger', 'view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        )
    ]
    tables_count = connection.execute("SELECT COUNT(*) FROM tables").fetchone()[0]
    print(
        json.dumps(
            {"format": "tasca-sqlite-logical-v2", "schema": schema, "tables_count": tables_count},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
except sqlite3.Error as error:
    raise SystemExit("database logical-state query failed") from error
finally:
    if connection is not None:
        connection.close()
PY
}

write_logical_baseline() {
    local python="$1"
    local target
    target="$(state_path database.logical.json)"
    [[ ! -e "$target" ]] || fail "database logical baseline is already sealed"
    "$python" -c '
import json
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.load(sys.stdin)
if (
    set(payload) != {"format", "schema", "tables_count"}
    or payload.get("format") != "tasca-sqlite-logical-v2"
    or not isinstance(payload["schema"], list)
    or not isinstance(payload["tables_count"], int)
    or payload["tables_count"] < 0
):
    raise SystemExit("database logical state has an invalid schema")
baseline = {
    "format": "tasca-sqlite-logical-v2",
    "schema": "tasca-0.1.29",
    "tables_count": payload["tables_count"],
}
temporary = path.with_name(f".{path.name}.tmp")
temporary.write_text(json.dumps(baseline, sort_keys=True, separators=(",", ":")) + "\n")
os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
os.replace(temporary, path)
os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
' "$target"
}

capture_database_identity() {
    local db_path
    db_path="$(normalized_db_path)"
    record_value database.path "$db_path"
    record_value database.identity "$(file_identity "$db_path")"
    record_value database.device_inode "$(file_device_inode "$db_path")"
    record_value database.disk "$(database_disk_source "$db_path")"
    record_value database.sha256 "$(file_hash "$db_path")"
}

capture_database_logical_baseline() {
    local python="$1"
    local db_path state
    db_path="$(normalized_db_path)"
    state="$(database_logical_state "$python" "$db_path")" \
        || fail "database logical state could not be captured"
    assert_exact_legacy_schema "$python" "$state" \
        || fail "SQLite schema does not match the exact 0.1.29 logical contract"
    printf '%s' "$state" | write_logical_baseline "$python"
}

assert_database_anchor() {
    local db_path expected_device_inode
    db_path="$(normalized_db_path)"
    [[ "$db_path" == "$(<"$(state_path database.path)")" ]] \
        || fail "database path changed from captured state"
    if [[ -f "$(state_path database.device_inode)" ]]; then
        expected_device_inode="$(<"$(state_path database.device_inode)")"
    else
        expected_device_inode="$(<"$(state_path database.identity)")"
        expected_device_inode="${expected_device_inode%:*}"
    fi
    [[ "$(file_device_inode "$db_path")" == "$expected_device_inode" ]] \
        || fail "database device or inode changed from captured state"
    if [[ -f "$(state_path database.disk)" ]]; then
        [[ "$(database_disk_source "$db_path")" == "$(<"$(state_path database.disk)")" ]] \
            || fail "database disk changed from captured state"
    fi
}

baseline_tables_row_count() {
    local python="$1"
    "$python" -c '
import json
import sys
payload = json.load(sys.stdin)
if (
    set(payload) != {"format", "schema", "tables_count"}
    or payload.get("format") != "tasca-sqlite-logical-v2"
    or payload.get("schema") != "tasca-0.1.29"
    or not isinstance(payload.get("tables_count"), int)
    or payload["tables_count"] < 0
):
    raise SystemExit("database logical baseline has an invalid schema")
print(payload["tables_count"])
'
}

assert_database_identity() {
    local python="$1"
    local db_path state expected_count current_count
    assert_database_anchor
    [[ -f "$(state_path database.logical.json)" ]] \
        || fail "database logical baseline is missing; resume requires explicit SQLite logical-state acceptance"
    db_path="$(normalized_db_path)"
    state="$(database_logical_state "$python" "$db_path")" \
        || fail "database logical state could not be read"
    assert_exact_legacy_schema "$python" "$state" \
        || fail "SQLite schema does not match the exact 0.1.29 logical contract"
    expected_count="$(baseline_tables_row_count "$python" < "$(state_path database.logical.json)")" \
        || fail "database logical baseline is invalid"
    current_count="$(logical_tables_row_count "$python" "$state")" \
        || fail "database logical state has no usable tables row count"
    [[ "$current_count" == "$expected_count" ]] \
        || fail "database tables row count changed from captured state"
}

health_payload() {
    local python="$1"
    local expected_version="$2"
    local expected_viewer="$3"
    "$python" -c '
import json
import sys
payload = json.load(sys.stdin)
expected_version, expected_viewer = sys.argv[1:]
if payload.get("version") != expected_version:
    raise SystemExit("health did not report the expected release version")
viewer = payload.get("viewer_auth_required")
if expected_version == "0.1.29" and expected_viewer == "false" and "viewer_auth_required" not in payload:
    raise SystemExit(0)
if type(viewer) is not bool:
    raise SystemExit("health did not report an explicit boolean Viewer mode")
if viewer is not (expected_viewer == "true"):
    raise SystemExit("health did not report the expected Viewer mode")
' "$expected_version" "$expected_viewer"
}

require_local_health() {
    local python="$1"
    local version="$2"
    local viewer="$3"
    local attempt retry_delay=1
    [[ "${TASCA_ROLLOUT_TESTING:-}" == "1" ]] && retry_delay=0
    for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
        if curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health \
            | health_payload "$python" "$version" "$viewer"; then
            return
        fi
        if ((attempt < HEALTH_ATTEMPTS && retry_delay > 0)); then
            sleep "$retry_delay"
        fi
    done
    fail "local Tasca health is not the expected release"
}

require_https_health() {
    local python="$1"
    local host="$2"
    local version="$3"
    local viewer="$4"
    local attempt retry_delay=1
    [[ "${TASCA_ROLLOUT_TESTING:-}" == "1" ]] && retry_delay=0
    for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
        if curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
            "https://${host}/api/v1/health" \
            | health_payload "$python" "$version" "$viewer"; then
            return
        fi
        if ((attempt < HEALTH_ATTEMPTS && retry_delay > 0)); then
            sleep "$retry_delay"
        fi
    done
    fail "HTTPS Tasca health is not the expected release"
}

logical_tables_row_count() {
    local python="$1"
    local state="$2"
    printf '%s' "$state" | "$python" -c '
import json
import sys
payload = json.load(sys.stdin)
count = payload.get("tables_count")
if not isinstance(count, int) or count < 0:
    raise SystemExit("database logical state has no usable tables row count")
print(count)
'
}

assert_exact_legacy_schema() {
    local python="$1"
    local state="$2"
    printf '%s' "$state" | "$python" -c '
import json
import sys
payload = json.load(sys.stdin)
expected_tables = {
    "dedup", "idempotency_keys", "patrons", "sayings", "sayings_fts",
    "sayings_fts_config", "sayings_fts_data", "sayings_fts_docsize", "sayings_fts_idx",
    "seats", "tables",
}
expected_objects = {("table", name) for name in expected_tables} | {
    ("index", "idx_seats_table_id"),
    ("index", "idx_seats_patron_id"),
    ("index", "idx_sayings_table_id"),
    ("index", "idx_sayings_table_sequence"),
    ("index", "idx_sayings_created_at"),
    ("index", "idx_tables_status"),
    ("index", "idx_dedup_first_seen"),
    ("index", "idx_idempotency_expires_at"),
    ("trigger", "sayings_ai"),
    ("trigger", "sayings_au"),
    ("trigger", "sayings_ad"),
}
actual_objects = {(item["type"], item["name"]) for item in payload["schema"]}
if actual_objects != expected_objects:
    raise SystemExit("SQLite schema does not match the exact 0.1.29 logical contract")
'
}

public_tables_row_count() {
    local python="$1"
    local host="$2"
    curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
        "https://${host}/api/v1/tables" \
        | "$python" -c 'import json, sys; payload = json.load(sys.stdin); assert isinstance(payload, list); print(len(payload))'
}

assert_public_tables_match_logical_state() {
    local python="$1"
    local host="$2"
    local state="$3"
    local sqlite_count public_count
    sqlite_count="$(logical_tables_row_count "$python" "$state")" \
        || fail "database logical state has no usable tables row count"
    public_count="$(public_tables_row_count "$python" "$host")" \
        || fail "public Tables API did not return a list"
    [[ "$public_count" == "$sqlite_count" ]] \
        || fail "public Tables API count does not match SQLite tables row count"
}

rollback_runtime_state() {
    local python="$1"
    local enabled active version
    enabled="$(systemctl is-enabled tasca.service 2>/dev/null || true)"
    active="$(systemctl is-active tasca.service 2>/dev/null || true)"
    [[ "$enabled" == "enabled" || "$enabled" == "disabled" ]] \
        || fail "service must have an enabled or disabled state"
    [[ "$active" == "active" ]] || fail "0.1.29 service must be active before release"
    version="$(curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health \
        | "$python" -c 'import json, sys; print(json.load(sys.stdin)["version"])')"
    [[ "$version" == "$ROLLBACK_VERSION" ]] || fail "running service must be ${ROLLBACK_VERSION} before release"
    printf '%s\n%s\n%s\n' "$enabled" "$active" "$version"
}

initialize_and_read_rollback_database() {
    local python="$1"
    curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/tables \
        | "$python" -c 'import json, sys; assert isinstance(json.load(sys.stdin), list)'
}

capture_service_state() {
    local python="$1"
    local state enabled active version
    state="$(rollback_runtime_state "$python")"
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
    local name
    for name in tasca.service tasca.env; do
        [[ -f "$(state_path "$name")" && -f "$(state_path "${name}.sha256")" && -f "$(state_path "${name}.mode")" ]] \
            || fail "rollback capture is incomplete"
    done
    [[ -f "$(state_path service.enabled)" && -f "$(state_path service.active)" && -f "$(state_path service.version)" ]] \
        || fail "rollback service capture is incomplete"
    [[ -f "$(state_path database.path)" && -f "$(state_path database.identity)" && -f "$(state_path database.sha256)" ]] \
        || fail "rollback database capture is incomplete"
    [[ "$(<"$(state_path service.enabled)")" == "enabled" || "$(<"$(state_path service.enabled)")" == "disabled" ]] \
        || fail "captured service enablement is invalid"
    [[ "$(<"$(state_path service.version)")" == "$ROLLBACK_VERSION" ]] \
        || fail "captured service version is not ${ROLLBACK_VERSION}"
    [[ "$(grep -c '^TASCA_VIEWER_TOKEN=' "$(state_path tasca.env)" || true)" == "0" ]] \
        || fail "rollback capture must keep TASCA_VIEWER_TOKEN absent"
}

verify_rollback_bundle() {
    local python="$1"
    local bundle="$2"
    local expected_sha="$3"
    [[ -f "$bundle" && "$expected_sha" =~ ^[[:xdigit:]]{64}$ ]] \
        || fail "rollback bundle input is invalid"
    [[ "$(file_hash "$bundle")" == "$expected_sha" ]] || fail "rollback bundle digest mismatch"
    "$python" - "$bundle" "$expected_sha" "$(file_hash "$0")" <<'PY'
import gzip
import hashlib
import json
import re
import sys
import tarfile

bundle, expected_digest, remote_digest = sys.argv[1:]
if hashlib.sha256(open(bundle, "rb").read()).hexdigest() != expected_digest:
    raise SystemExit("rollback bundle digest mismatch")
with tarfile.open(bundle, "r:gz") as archive:
    members = archive.getmembers()
    if not members or any(not member.isfile() or not member.name.startswith("rollback/") for member in members):
        raise SystemExit("rollback bundle member path is invalid")
    content = {member.name: archive.extractfile(member).read() for member in members}
try:
    manifest = json.loads(content["rollback/manifest.json"])
except (KeyError, json.JSONDecodeError) as error:
    raise SystemExit("rollback bundle manifest is invalid") from error
if not isinstance(manifest, dict) or manifest.get("format") != "tasca-rollback-bundle-v1":
    raise SystemExit("rollback bundle format is invalid")
rollback = manifest.get("rollback")
producer = manifest.get("producer")
artifacts = manifest.get("artifacts")
if (
    not isinstance(rollback, dict)
    or rollback.get("version") != "0.1.29"
    or rollback.get("python_requires") != ">=3.13"
    or rollback.get("constraints") != {"fastmcp": "<4", "httpx": "required"}
    or not isinstance(producer, dict)
    or not isinstance(artifacts, list)
):
    raise SystemExit("rollback bundle contract is invalid")
files = producer.get("files")
if not isinstance(files, list) or not any(
    isinstance(item, dict)
    and item.get("path") == "scripts/gcp/viewer-auth-remote.sh"
    and item.get("sha256") == remote_digest
    for item in files
):
    raise SystemExit("rollback bundle remote producer digest is mismatched")
requirements = []
names = set()
for artifact in artifacts:
    if not isinstance(artifact, dict):
        raise SystemExit("rollback artifact metadata is invalid")
    filename = artifact.get("filename")
    name = artifact.get("name")
    version = artifact.get("version")
    digest = artifact.get("sha256")
    if (
        not isinstance(filename, str)
        or "/" in filename
        or not isinstance(name, str)
        or not isinstance(version, str)
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or name in names
    ):
        raise SystemExit("rollback artifact metadata is invalid")
    names.add(name)
    payload = content.get(f"rollback/wheelhouse/{filename}")
    if payload is None or hashlib.sha256(payload).hexdigest() != digest:
        raise SystemExit("rollback artifact digest is mismatched")
    requirements.append(f"{name}=={version} --hash=sha256:{digest}\n")
fastmcp = next((item for item in artifacts if item.get("name") == "fastmcp"), None)
if "tasca" not in names or "httpx" not in names or not isinstance(fastmcp, dict):
    raise SystemExit("rollback bundle is missing Tasca, httpx, or fastmcp")
try:
    fastmcp_major = int(str(fastmcp["version"]).split(".", 1)[0])
except (KeyError, ValueError):
    raise SystemExit("rollback bundle fastmcp version is invalid")
if fastmcp_major >= 4:
    raise SystemExit("rollback bundle fastmcp selection violates <4")
if rollback.get("wheel_sha256") != next((item["sha256"] for item in artifacts if item.get("name") == "tasca"), None):
    raise SystemExit("rollback Tasca wheel digest is mismatched")
if content.get("rollback/requirements.txt") != "".join(requirements).encode():
    raise SystemExit("rollback requirements do not bind artifact bytes")
PY
}

extract_rollback_bundle() {
    local bundle="$1"
    local directory
    directory="$(mktemp -d "${ROOT_PREFIX}/run/tasca-rollback-input.XXXXXX")"
    tar -xzf "$bundle" -C "$directory"
    printf '%s\n' "$directory"
}

install_rollback_bundle() {
    local python="$1"
    local bundle="$2"
    local bundle_sha="$3"
    local unpacked
    require_python "$python"
    verify_rollback_bundle "$python" "$bundle" "$bundle_sha"
    unpacked="$(extract_rollback_bundle "$bundle")"
    trap 'rm -rf "$unpacked"' RETURN
    command -v uv >/dev/null || fail "uv must be installed before rollback"
    install -d -o tasca -g tasca -m 0700 "$ROLLBACK_RELEASE_DIR"
    uv venv --clear --python "$python" "${ROLLBACK_RELEASE_DIR}/venv"
    uv pip install --offline --no-index --find-links "${unpacked}/rollback/wheelhouse" \
        --require-hashes --python "${ROLLBACK_RELEASE_DIR}/venv/bin/python" \
        -r "${unpacked}/rollback/requirements.txt"
    chown -R tasca:tasca "${ROLLBACK_RELEASE_DIR}/venv"
    [[ -x "${ROLLBACK_RELEASE_DIR}/venv/bin/tasca" ]] \
        || fail "offline rollback bundle did not install the Tasca executable"
    rm -rf "$unpacked"
    trap - RETURN
}

preflight() {
    local rollback_version=""
    local python=""
    local bundle=""
    local bundle_sha=""
    while (($#)); do
        case "$1" in
            --rollback-version) rollback_version="${2:-}"; shift 2 ;;
            --python) python="${2:-}"; shift 2 ;;
            --rollback-bundle) bundle="${2:-}"; shift 2 ;;
            --rollback-sha) bundle_sha="${2:-}"; shift 2 ;;
            *) fail "unknown preflight argument: $1" ;;
        esac
    done
    require_version "$rollback_version"
    require_python "$python"
    verify_rollback_bundle "$python" "$bundle" "$bundle_sha"
    mountpoint -q "$DATA_DIR" || fail "persistent tasca-data disk is not mounted"
    [[ -f "$UNIT_FILE" ]] || fail "current service unit is missing"
    [[ -f "$ENV_FILE" ]] || fail "current service environment is missing"
    [[ ! -e "$BACKUP_DIR" ]] || fail "rollback inputs already exist; refusing to replace them"
    [[ "$(grep -c '^TASCA_VIEWER_TOKEN=' "$ENV_FILE" || true)" == "0" ]] \
        || fail "0.1.29 rollback environment must keep Viewer reads public"
    rollback_runtime_state "$python" >/dev/null
    initialize_and_read_rollback_database "$python"
    normalized_db_path >/dev/null
    install -d -m 0700 "$BACKUP_DIR"
    record_file tasca.service "$UNIT_FILE"
    record_file tasca.env "$ENV_FILE"
    capture_database_identity
    capture_database_logical_baseline "$python"
    capture_service_state "$python"
    printf 'tasca remote rollout: exact 0.1.29 runtime and rollback inputs captured\n'
}

ensure_tls() {
    local host="$1"
    local verification
    validate_host "$host"
    verification="$(curl --silent --show-error --proto '=https' --tlsv1.2 \
        --resolve "${host}:443:127.0.0.1" --output /dev/null --write-out '%{ssl_verify_result}' \
        "https://${host}/")" || fail "HTTPS certificate transport is not active"
    [[ "$verification" == "0" ]] || fail "HTTPS certificate validation failed"
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
    printf 'tasca remote rollout: certificate-valid HTTPS is active without backend health\n'
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
    install -o root -g tasca -m 0640 "$output_environment" "$ENV_FILE"
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

write_rollback_environment() {
    local temporary_dir="$1"
    local base_environment="${temporary_dir}/base.env"
    local output_environment="${temporary_dir}/tasca.env"
    grep -Ev '^TASCA_(ADMIN_TOKEN|VIEWER_TOKEN|API_HOST|API_PORT)=' "$(state_path tasca.env)" \
        > "$base_environment" || true
    grep -q '^TASCA_DB_PATH=' "$base_environment" || fail "rollback environment lost database path"
    cat "$base_environment" > "$output_environment"
    printf 'TASCA_API_HOST=127.0.0.1\nTASCA_API_PORT=8000\nTASCA_ADMIN_TOKEN=' >> "$output_environment"
    cat "${temporary_dir}/admin" >> "$output_environment"
    printf '\n' >> "$output_environment"
    grep -q '^TASCA_VIEWER_TOKEN=' "$output_environment" \
        && fail "deterministic rollback environment must keep Viewer reads public"
    install -d -m 0700 "${ROOT_PREFIX}/etc/tasca"
    install -o root -g tasca -m 0640 "$output_environment" "$ENV_FILE"
}

write_rollback_unit() {
    local python="$1"
    local replacement="${ROLLBACK_RELEASE_DIR}/venv/bin/tasca"
    cp --preserve=mode -- "$(state_path tasca.service)" "$UNIT_FILE"
    "$python" - "$UNIT_FILE" "$replacement" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
replacement = sys.argv[2]
lines = path.read_text().splitlines()
positions = [index for index, line in enumerate(lines) if line.startswith("ExecStart=")]
if len(positions) != 1:
    raise SystemExit("rollback unit must have exactly one ExecStart")
lines[positions[0]] = f"ExecStart={replacement}"
path.write_text("\n".join(lines) + "\n")
PY
    chmod "$(<"$(state_path tasca.service.mode)")" "$UNIT_FILE"
}

apply_release() {
    local host=""
    local wheel=""
    local wheel_sha=""
    local release_version=""
    local viewer_mode=""
    local python=""
    local bundle=""
    local bundle_sha=""
    while (($#)); do
        case "$1" in
            --https-host) host="${2:-}"; shift 2 ;;
            --wheel) wheel="${2:-}"; shift 2 ;;
            --wheel-sha) wheel_sha="${2:-}"; shift 2 ;;
            --release-version) release_version="${2:-}"; shift 2 ;;
            --viewer-mode) viewer_mode="${2:-}"; shift 2 ;;
            --python) python="${2:-}"; shift 2 ;;
            --rollback-bundle) bundle="${2:-}"; shift 2 ;;
            --rollback-sha) bundle_sha="${2:-}"; shift 2 ;;
            *) fail "unknown apply argument: $1" ;;
        esac
    done
    viewer_mode="$(normalize_viewer_mode "$viewer_mode")"
    validate_host "$host"
    require_python "$python"
    [[ "$release_version" == "$RELEASE_VERSION" ]] || fail "release version must be ${RELEASE_VERSION}"
    [[ "$(basename -- "$wheel")" == "tasca-${RELEASE_VERSION}-py3-none-any.whl" ]] \
        || fail "exact 0.1.30 wheel is required"
    [[ -f "$wheel" && "$wheel_sha" =~ ^[[:xdigit:]]{64}$ ]] || fail "release wheel input is invalid"
    [[ "$(file_hash "$wheel")" == "$wheel_sha" ]] || fail "staged release wheel digest mismatch"
    assert_backup_integrity
    assert_database_identity "$python"
    verify_rollback_bundle "$python" "$bundle" "$bundle_sha"
    ensure_tls "$host"

    local temporary_dir expected_viewer_auth
    temporary_dir="$(mktemp -d "${ROOT_PREFIX}/run/tasca-rollout.XXXXXX")"
    trap 'rm -rf "$temporary_dir"' EXIT
    secret_to_file tasca-admin-token "${temporary_dir}/admin"
    if [[ "$viewer_mode" == "configured" ]]; then
        secret_to_file tasca-viewer-token "${temporary_dir}/viewer"
        cmp -s "${temporary_dir}/admin" "${temporary_dir}/viewer" \
            && fail "configured credentials must differ"
        printf 'tasca remote rollout: configured Admin/Viewer credentials differ: true\n'
        expected_viewer_auth=true
    else
        expected_viewer_auth=false
    fi

    command -v uv >/dev/null || fail "uv must be installed before release activation"
    install -d -o tasca -g tasca -m 0700 "$RELEASE_DIR"
    uv venv --clear --python "$python" "${RELEASE_DIR}/venv"
    uv pip install --python "${RELEASE_DIR}/venv/bin/python" "$wheel"
    chown -R tasca:tasca "${RELEASE_DIR}/venv"
    [[ -x "${RELEASE_DIR}/venv/bin/tasca" ]] || fail "exact 0.1.30 wheel did not install Tasca"
    write_release_environment "$temporary_dir" "$viewer_mode"
    write_release_unit
    systemctl daemon-reload
    systemctl restart tasca.service
    require_local_health "$python" "$RELEASE_VERSION" "$expected_viewer_auth"
    require_https_health "$python" "$host" "$RELEASE_VERSION" "$expected_viewer_auth"
    assert_database_identity "$python"
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

rollback_release() {
    local rollback_version=""
    local host=""
    local python=""
    local bundle=""
    local bundle_sha=""
    while (($#)); do
        case "$1" in
            --rollback-version) rollback_version="${2:-}"; shift 2 ;;
            --https-host) host="${2:-}"; shift 2 ;;
            --python) python="${2:-}"; shift 2 ;;
            --rollback-bundle) bundle="${2:-}"; shift 2 ;;
            --rollback-sha) bundle_sha="${2:-}"; shift 2 ;;
            *) fail "unknown rollback argument: $1" ;;
        esac
    done
    require_version "$rollback_version"
    validate_host "$host"
    require_python "$python"
    assert_backup_integrity
    assert_database_identity "$python"
    ensure_tls "$host"
    local temporary_dir
    temporary_dir="$(mktemp -d "${ROOT_PREFIX}/run/tasca-rollback.XXXXXX")"
    trap 'rm -rf "$temporary_dir"' EXIT
    secret_to_file tasca-admin-token "${temporary_dir}/admin"
    install_rollback_bundle "$python" "$bundle" "$bundle_sha"
    write_rollback_environment "$temporary_dir"
    write_rollback_unit "$python"
    systemctl daemon-reload
    restore_enablement
    systemctl restart tasca.service
    require_local_health "$python" "$ROLLBACK_VERSION" false
    require_https_health "$python" "$host" "$ROLLBACK_VERSION" false
    verify_public_read --https-host "$host" --python "$python"
    assert_database_identity "$python"
    rm -rf "$temporary_dir"
    trap - EXIT
    printf 'tasca remote rollout: exact 0.1.29 public-read runtime restored from offline bundle\n'
}

reconcile_release() {
    local host=""
    local wheel=""
    local wheel_sha=""
    local release_version=""
    local viewer_mode=""
    local python=""
    local bundle=""
    local bundle_sha=""
    while (($#)); do
        case "$1" in
            --https-host) host="${2:-}"; shift 2 ;;
            --wheel) wheel="${2:-}"; shift 2 ;;
            --wheel-sha) wheel_sha="${2:-}"; shift 2 ;;
            --release-version) release_version="${2:-}"; shift 2 ;;
            --viewer-mode) viewer_mode="${2:-}"; shift 2 ;;
            --python) python="${2:-}"; shift 2 ;;
            --rollback-bundle) bundle="${2:-}"; shift 2 ;;
            --rollback-sha) bundle_sha="${2:-}"; shift 2 ;;
            *) fail "unknown reconcile argument: $1" ;;
        esac
    done
    viewer_mode="$(normalize_viewer_mode "$viewer_mode")"
    validate_host "$host"
    require_python "$python"
    [[ "$release_version" == "$RELEASE_VERSION" ]] || fail "release version must be ${RELEASE_VERSION}"
    [[ "$(basename -- "$wheel")" == "tasca-${RELEASE_VERSION}-py3-none-any.whl" && -f "$wheel" ]] \
        || fail "resume requires the exact staged 0.1.30 wheel"
    [[ "$wheel_sha" =~ ^[[:xdigit:]]{64}$ && "$(file_hash "$wheel")" == "$wheel_sha" ]] \
        || fail "resume refuses a mismatched release wheel digest"
    assert_backup_integrity
    assert_database_identity "$python"
    verify_rollback_bundle "$python" "$bundle" "$bundle_sha"
    ensure_tls "$host"
    require_local_health "$python" "$RELEASE_VERSION" "$( [[ "$viewer_mode" == configured ]] && printf true || printf false )"
    require_https_health "$python" "$host" "$RELEASE_VERSION" "$( [[ "$viewer_mode" == configured ]] && printf true || printf false )"
    printf 'tasca remote rollout: existing exact 0.1.30 state reconciled\n'
}

rehearse_release() {
    reconcile_release "$@"
    local host=""
    local wheel=""
    local wheel_sha=""
    local release_version=""
    local viewer_mode=""
    local python=""
    local bundle=""
    local bundle_sha=""
    while (($#)); do
        case "$1" in
            --https-host) host="${2:-}"; shift 2 ;;
            --wheel) wheel="${2:-}"; shift 2 ;;
            --wheel-sha) wheel_sha="${2:-}"; shift 2 ;;
            --release-version) release_version="${2:-}"; shift 2 ;;
            --viewer-mode) viewer_mode="${2:-}"; shift 2 ;;
            --python) python="${2:-}"; shift 2 ;;
            --rollback-bundle) bundle="${2:-}"; shift 2 ;;
            --rollback-sha) bundle_sha="${2:-}"; shift 2 ;;
            *) fail "unknown rehearse argument: $1" ;;
        esac
    done
    rollback_release --rollback-version "$ROLLBACK_VERSION" --https-host "$host" --python "$python" \
        --rollback-bundle "$bundle" --rollback-sha "$bundle_sha"
    apply_release --https-host "$host" --wheel "$wheel" --wheel-sha "$wheel_sha" \
        --release-version "$release_version" --viewer-mode "$viewer_mode" --python "$python" \
        --rollback-bundle "$bundle" --rollback-sha "$bundle_sha"
}

reseal_sqlite_logical_state() {
    local host=""
    local python=""
    while (($#)); do
        case "$1" in
            --https-host) host="${2:-}"; shift 2 ;;
            --python) python="${2:-}"; shift 2 ;;
            *) fail "unknown reseal-sqlite-logical-state argument: $1" ;;
        esac
    done
    require_python "$python"
    validate_host "$host"
    assert_backup_integrity
    [[ ! -e "$(state_path database.logical.json)" ]] \
        || fail "database logical baseline is already sealed"
    assert_database_anchor
    ensure_tls "$host"
    require_local_health "$python" "$ROLLBACK_VERSION" false
    require_https_health "$python" "$host" "$ROLLBACK_VERSION" false
    local db_path state
    db_path="$(normalized_db_path)"
    state="$(database_logical_state "$python" "$db_path")" \
        || fail "database logical state could not be read"
    assert_exact_legacy_schema "$python" "$state" \
        || fail "SQLite schema does not match the exact 0.1.29 logical contract"
    assert_public_tables_match_logical_state "$python" "$host" "$state"
    printf '%s' "$state" | write_logical_baseline "$python"
    printf 'tasca remote rollout: current exact 0.1.29 SQLite logical state sealed\n'
}

verify_public_read() {
    local host=""
    local python=""
    while (($#)); do
        case "$1" in
            --https-host) host="${2:-}"; shift 2 ;;
            --python) python="${2:-}"; shift 2 ;;
            *) fail "unknown verify-public-read argument: $1" ;;
        esac
    done
    require_python "$python"
    assert_backup_integrity
    assert_database_identity "$python"
    ensure_tls "$host"
    require_local_health "$python" "$ROLLBACK_VERSION" false
    require_https_health "$python" "$host" "$ROLLBACK_VERSION" false
    local db_path state
    db_path="$(normalized_db_path)"
    state="$(database_logical_state "$python" "$db_path")" \
        || fail "database logical state could not be read"
    assert_public_tables_match_logical_state "$python" "$host" "$state"
    assert_database_identity "$python"
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
        reconcile) reconcile_release "$@" ;;
        rehearse) rehearse_release "$@" ;;
        rollback) rollback_release "$@" ;;
        reseal-sqlite-logical-state) reseal_sqlite_logical_state "$@" ;;
        verify-public-read) verify_public_read "$@" ;;
        *) fail "unknown action: $action" ;;
    esac
}

main "$@"
