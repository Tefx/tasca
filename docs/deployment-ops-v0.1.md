# Deployment & Ops (v0.1)

> Scope: one Tasca API process on one persistent SQLite disk. The tracked GCP
> producers describe a reversible deployment; they do not publish a package.

## Deployment constraints (normative)

- Run exactly one API process per `TASCA_DB_PATH`. Tasca v0.1 does not support
  multiple SQLite writers.
- Keep the existing `tasca-data` Persistent Disk mounted at `/var/lib/tasca`.
  Release and rollback producers never format, copy, migrate, delete, or replace
  SQLite bytes.
- The GCP target is `rda-engineering`, `asia-southeast1-b`, VM `tasca-mcp`.
  The rollout does not create another VM, worker, or database.
- Bind the backend to `127.0.0.1:8000`; TLS terminates at Caddy on the approved
  hostname. A priority-0 ingress deny scoped to the target VPC and `tasca-mcp`
  tag blocks public TCP/8000 before any allow rule can match. HTTP may only be
  used for credential-free redirects; credentials travel in HTTPS
  `Authorization` headers only.

## Configuration and access

`TASCA_ADMIN_TOKEN` always protects admin mutations and MCP HTTP. An absent,
blank, `null`, `none`, or `clear` admin setting generates an ephemeral local
admin token; it does not disable authentication.

`TASCA_VIEWER_TOKEN` is optional. An absent, blank, `null`, `none`, or `clear`
viewer setting restores public REST resource reads. When configured, a REST
resource route accepts a Viewer or Admin Bearer credential; admin-only mutations
still require Admin. Viewer and Admin credentials must differ.

The following remain public whether Viewer auth is enabled or disabled:

- `/api/v1/health` and `/api/v1/ready`
- `/docs` and `/openapi.json`
- the SPA shell and its static assets

The health payload exposes only `viewer_auth_required`; never log, print, put in
a URL, or pass either credential on a command line. The SPA validates a Viewer or
Admin credential with `GET /api/v1/auth/validate` before it loads protected data.
It can elevate from Viewer to Admin with a fresh validation. See
[`tasca-http-api-v0.1.md`](tasca-http-api-v0.1.md) and
[`tasca-web-uiux-v0.1.md`](tasca-web-uiux-v0.1.md) for route and UI behavior.

## Build the 0.1.30 release candidate

From a clean committed checkout, build the tracked SPA before building Python
artifacts:

```bash
uv sync --frozen --group dev
(cd web && npm ci && npm test && npm run lint && npm run build)
uv run pytest -q
uv build
shasum -a 256 dist/tasca-0.1.30-py3-none-any.whl
git rev-parse HEAD
```

The wheel filename, its SHA-256, and the committed producer revision are the
release identity. The wheel includes `tasca/web/dist/`; no `uvx --from
tasca==...`, PyPI `latest`, or untracked `.artifacts` helper is an installation
input. Building creates local `dist/` artifacts only. It does not publish or
deploy them.

## Build and verify the immutable 0.1.29 rollback input

The rollback input starts with a declared immutable wheelhouse for the target
Linux/CPython 3.13 runtime. It must contain the exact
`tasca-0.1.29-py3-none-any.whl` release wheel and every resolved dependency
wheel, including `httpx`; do not ask `uvx`, `uv`, or pip to resolve a package at
rollback time. Store these release inputs and generated output outside Git.

From a clean committed checkout, build the content-addressed archive and its
0600 token-free receipt. The command rejects a wheelhouse with missing declared
dependencies, duplicate package versions, a non-0.1.29 Tasca wheel, a different
wheel hash, no `httpx`, no selected `fastmcp<4`, a FastMCP 4.x wheel, or a
Tasca wheel whose `Requires-Python` is not `>=3.13`. The receipt records the
required `fastmcp<4` constraint and selected FastMCP/httpx wheels, preventing a
later input from silently selecting FastMCP 4.

```bash
ROLLBACK_INPUT_DIR=/secure/local/immutable-tasca-0.1.29
ROLLBACK_OUTPUT_DIR=/tmp/tasca-rollback-input
mkdir -p "$ROLLBACK_OUTPUT_DIR"
ROLLBACK_WHEEL="$ROLLBACK_INPUT_DIR/wheelhouse/tasca-0.1.29-py3-none-any.whl"
ROLLBACK_WHEEL_SHA256="$(sha256sum "$ROLLBACK_WHEEL" | awk '{print $1}')"

bash scripts/gcp/build-viewer-auth-rollback-bundle.sh build \
  --wheel "$ROLLBACK_WHEEL" --wheel-sha256 "$ROLLBACK_WHEEL_SHA256" \
  --wheelhouse "$ROLLBACK_INPUT_DIR/wheelhouse" \
  --output "$ROLLBACK_OUTPUT_DIR/tasca-0.1.29-rollback.tar.gz" \
  --receipt "$ROLLBACK_OUTPUT_DIR/tasca-0.1.29-rollback-receipt.json"

TASCA_ROLLBACK_SHA256="$(sha256sum "$ROLLBACK_OUTPUT_DIR/tasca-0.1.29-rollback.tar.gz" | awk '{print $1}')"
bash scripts/gcp/build-viewer-auth-rollback-bundle.sh verify \
  --bundle "$ROLLBACK_OUTPUT_DIR/tasca-0.1.29-rollback.tar.gz" \
  --sha256 "$TASCA_ROLLBACK_SHA256"

# The smoke creates a temporary 0700 loopback environment. It installs only
# archive-contained wheels with uv --offline --no-index --require-hashes,
# starts exact 0.1.29, checks public Viewer health, and reads /api/v1/tables
# without a Viewer token.
bash scripts/gcp/build-viewer-auth-rollback-bundle.sh smoke \
  --bundle "$ROLLBACK_OUTPUT_DIR/tasca-0.1.29-rollback.tar.gz" \
  --sha256 "$TASCA_ROLLBACK_SHA256" \
  --python /absolute/path/to/cpython-3.13
```

When the archive contains Linux x86_64 wheels and the local host has another
platform, run `verify` on the host first, then run the same tracked smoke in an
isolated Linux x86_64 container. The container has no network and receives only
read-only source and bundle mounts; `--allow-detached-producer` is limited to
this second artifact-runtime check because the host `verify` already matched the
bundle producer to the checkout.

```bash
docker run --rm --network none --platform linux/amd64 \
  -v "$PWD:/repo:ro" \
  -v "$ROLLBACK_OUTPUT_DIR/tasca-0.1.29-rollback.tar.gz:/input/rollback.tar.gz:ro" \
  -w /repo ghcr.io/astral-sh/uv:python3.13-bookworm-slim \
  bash scripts/gcp/build-viewer-auth-rollback-bundle.sh smoke \
  --bundle /input/rollback.tar.gz --sha256 "$TASCA_ROLLBACK_SHA256" \
  --python /usr/local/bin/python3.13 --allow-detached-producer
```

The archive and receipt bind the exact Tasca and dependency bytes, their
SHA-256 values, `>=3.13`, and the revision plus SHA-256 values of the tracked
rollout, remote, and bundle producers. The receipt contains no package paths,
credentials, tokens, or environment values. Keep the archive SHA-256 with the
release receipt; the later runtime command supplies it through
`TASCA_ROLLBACK_BUNDLE` and `TASCA_ROLLBACK_SHA256`.

## Secret lifecycle

Create the two Secret Manager resources once under the approved project, then
add/rotate versions through a secret-safe stdin channel. Do not place a value in
shell history, a URL, a command argument, source, test fixture, or log.

```bash
gcloud secrets create tasca-admin-token --project=rda-engineering --replication-policy=automatic
gcloud secrets create tasca-viewer-token --project=rda-engineering --replication-policy=automatic
read -r -s TASCA_NEW_TOKEN
printf %s "$TASCA_NEW_TOKEN" | gcloud secrets versions add tasca-viewer-token --project=rda-engineering --data-file=-
unset TASCA_NEW_TOKEN
```

For an existing secret, omit the matching `gcloud secrets create` command and
add a new version. Generate Admin and Viewer values independently, verify their
normalized values differ without displaying either, and retain the prior Admin
secret until the new Admin version is active and final health succeeds. The
explicit `resume --rotate-admin-secret-version` lifecycle creates one new
`tasca-admin-token` version through a stdin channel, uses its `latest` value for
the rollback rehearsal and final activation, then disables version 1 only after
final health succeeds. If a failed rehearsal already left version 1 plus one
post-version-1 Admin version enabled, a later `resume` reuses that version and
does not add another; one enabled post-version-1 version means the rotation is
already finalized. Other enabled Admin layouts stop for reconciliation. Resume
also requires exactly enabled Viewer version 1. A public Viewer rollout does not read the Viewer secret
at all. A configured Viewer rollout reads it only after the TLS gate passes; the
release environment is root-owned, group-readable only by `tasca`, and mode
0640 without exposing either credential.

## Exact GCP rollout and rollback
Before `apply`, witness that the chosen hostname points at the existing VM and
can obtain a valid certificate. The tracked producer stages Caddy and verifies
certificate-valid HTTPS before it asks Secret Manager for either credential. It
then resolves the target VM's actual VPC network and `tasca-mcp` tag, readbacks
or replaces the named priority-0 TCP/8000 denial on that exact `--network`, and
starts one loopback-bound systemd service. The denial preempts public allow rules
without deleting them. It does not alter public HTTPS/443 or add an IP-specific
TCP/8000 exception.

The later runtime-only repair resumes the already-active partial target with the
following exact command. `TASCA_PYTHON` names the admitted uv-managed CPython
3.13 binary on the VM; the producer rejects `/usr/bin/python3`, any non-CPython
runtime, and every minor version except 3.13 before TLS, credential, or service
effects.

```bash
env PROJECT_ID=rda-engineering ZONE=asia-southeast1-b VM=tasca-mcp \
  TASCA_HTTPS_HOST=34.1.134.239.sslip.io \
  TASCA_VIEWER_MODE=configured \
  TASCA_PYTHON=/var/lib/tasca/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13 \
  RELEASE_WHEEL=dist/tasca-0.1.30-py3-none-any.whl \
  RELEASE_SHA256=<release-receipt-sha256> \
  TASCA_ROLLBACK_BUNDLE=<repair-receipt-artifact> \
  TASCA_ROLLBACK_SHA256=<repair-receipt-sha256> \
  scripts/gcp/viewer-auth-rollout.sh resume \
  --require-version 0.1.30 --rollback-version 0.1.29 \
  --rehearse-rollback --rotate-admin-secret-version \
  --accept-current-sqlite-logical-state
```

`resume` first performs read-only VM/VPC/tag, priority-0 TCP/8000 deny, Viewer
secret-version-1, release-wheel, rollback-bundle, rollback-capture, local-health,
and HTTPS-health reconciliation. A difference stops the lifecycle before a new
runtime effect. When all state matches, it stages only the tracked producer and
immutable bytes; it does not reconfigure Caddy, replay the matching firewall
denial, or add/read a Viewer secret version. The explicit Admin rotation creates
version 2 from a secret-safe stdin channel when version 1 is the only enabled
Admin version. A recovered state with versions 1 and 2 enabled reuses version 2
and does not create version 3. In either case, it disables version 1 only after
final 0.1.30 proof.

`--accept-current-sqlite-logical-state` is a one-time, user-authorized `resume`
option for a historical 0.1.29 capture that lacks a logical SQLite baseline. It
reseals before any release, service, or secret mutation. Resealing requires the
captured path and device/inode anchor, the mounted `tasca-data` disk, exact local
and HTTPS public 0.1.29 health, `PRAGMA integrity_check=ok`, and an
unauthenticated REST `/tables` count equal to SQLite's `tables` row count. It
atomically writes a 0600 token-free record containing only the logical-state
format, deployed schema label, and `tables` count. The producer checks the exact
expected SQLite schema object names and types directly; it does not record or
hash row values, FTS rows, idempotency rows, or TTL rows. Any version, integrity,
schema, path, device/inode, or API-count mismatch fails before the seal. Later
release/rollback checks require the same schema and domain-table count, so normal
WAL/checkpoint main-file size or SHA-256 changes are forensic metadata rather
than deployment gates. The flag is single-use: after a successful reseal, the
0600 baseline already exists. If a later effect fails, reconcile observed state
and retry `resume` without `--accept-current-sqlite-logical-state`.

For a fresh 0.1.29 baseline, `apply` uses the same environment and immutable
bundle with `apply --require-version 0.1.30 --rollback-version 0.1.29
--rehearse-rollback`. It preflights the selected Python and rollback input before
Caddy, credential, firewall, or service effects. Caddy TLS readiness verifies a
valid certificate without requiring a healthy backend, allowing recovery from a
502 while Tasca is stopped. Activation later requires both real local
`127.0.0.1:8000` and certificate-valid HTTPS health responses from the exact
0.1.30 service.

Before capture, the remote producer initializes and reads the active 0.1.29
database. It refuses a baseline containing `TASCA_VIEWER_TOKEN`. During
rollback it installs the archive's hash-pinned wheel set with `uv --offline
--no-index --require-hashes`, rewrites only the captured unit's `ExecStart` to
the deterministic 0.1.29 venv, rewrites the environment with the current Admin
secret and no Viewer token, and preserves captured service enablement plus
restricted environment mode. The final database check occurs after the public HTTPS read. It preserves
captured main-file device/inode/size/SHA-256 as forensic metadata, enforces the
path and device/inode anchor, and checks the deployed schema plus the baseline
`tables` count. WAL/checkpoint physical byte changes therefore remain deployable
while schema or domain-table-count changes caused by that read fail rollback
verification.

To perform only the deterministic rollback, use the same explicit Python and
bundle inputs with `viewer-auth-rollout.sh rollback --rollback-version 0.1.29`.
## Mandatory persistence verification protocol

The verifier uses a 0600, token-free, bounded state receipt. It contains only
format, phase, HTTPS base URL, expected version, and table ID. It stores no
credential or service log. Override its default runtime receipt location only
through `TASCA_VIEWER_AUTH_STATE_FILE` when the rollout and verifier run from
different local environments.

1. After the configured Viewer/Admin release is reachable, prepare the exact
   MCP `table_create` and `table_get` persistence witness:

   ```bash
   env TASCA_HTTPS_BASE_URL=https://<witnessed-host> \
     uv run python scripts/gcp/verify_viewer_auth_remote.py \
       --project rda-engineering --viewer-secret tasca-viewer-token \
       --admin-secret tasca-admin-token --expected-version 0.1.30 \
       --check-direct-port 34.1.134.239:8000 --prepare
   ```

2. Reapply with the rollout-owned restart and mark the prepared receipt. This
   action requires the normal exact wheel inputs and Viewer mode:

   ```bash
   env PROJECT_ID=rda-engineering ZONE=asia-southeast1-b VM=tasca-mcp \
     TASCA_HTTPS_HOST=<witnessed-host> TASCA_VIEWER_MODE=configured \
     TASCA_PYTHON=<admitted-cpython-3.13-path> \
     RELEASE_WHEEL=dist/tasca-0.1.30-py3-none-any.whl \
     RELEASE_SHA256=<recorded-wheel-sha256> \
     TASCA_ROLLBACK_BUNDLE=<immutable-rollback-bundle> \
     TASCA_ROLLBACK_SHA256=<recorded-rollback-bundle-sha256> \
     scripts/gcp/viewer-auth-rollout.sh reapply \
       --require-version 0.1.30 --rollback-version 0.1.29 \
       --verification-state "${TASCA_VIEWER_AUTH_STATE_FILE:-${XDG_RUNTIME_DIR:-/tmp}/tasca-viewer-auth-state.json}"
   ```

3. Run the downstream cleanup command exactly as selected by the release
   receipt:

   ```bash
   env TASCA_HTTPS_BASE_URL=https://<witnessed-host> uv run python scripts/gcp/verify_viewer_auth_remote.py --project rda-engineering --viewer-secret tasca-viewer-token --admin-secret tasca-admin-token --expected-version 0.1.30 --check-direct-port 34.1.134.239:8000 --cleanup
   ```

Both phases require certificate-valid HTTPS/443 and direct public TCP/8000
refusal. Prepare verifies public/static paths; configured REST unauthenticated,
Viewer, and Admin read/mutation boundaries; unauthenticated and Viewer MCP
denial; both required tools; and real Admin MCP create/exact-ID get. Cleanup
requires the rollout reapply mark, repeats configured access checks, performs
an exact MCP readback, deletes the table, verifies its explicit absence, scans
retained verifier output and fetched `tasca.service` log evidence for either
configured credential, then removes the state receipt. In public mode, the same
REST matrix also requires unauthenticated mutation denial.
## Backups and observability

Back up SQLite by stopping the service or using SQLite's online backup API; do
not copy a live database file casually. Operational logs should contain table
and request metadata only. Scan rollout receipts and service logs for credential
values before sharing evidence.
