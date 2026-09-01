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
secret for rollback. A public Viewer rollout does not read the Viewer secret at
all. A configured Viewer rollout reads it only after the TLS gate passes; the
release environment is root-written without exposing either credential.

## Exact GCP rollout and rollback
Before `apply`, witness that the chosen hostname points at the existing VM and
can obtain a valid certificate. The tracked producer stages Caddy and verifies
certificate-valid HTTPS before it asks Secret Manager for either credential. It
then resolves the target VM's actual VPC network and `tasca-mcp` tag, readbacks
or replaces the named priority-0 TCP/8000 denial on that exact `--network`, and
starts one loopback-bound systemd service. The denial preempts public allow rules
without deleting them. It does not alter public HTTPS/443 or add an IP-specific
TCP/8000 exception.

```bash
env PROJECT_ID=rda-engineering ZONE=asia-southeast1-b VM=tasca-mcp \
  TASCA_HTTPS_HOST=<approved-hostname> \
  TASCA_VIEWER_MODE=configured \
  RELEASE_WHEEL=dist/tasca-0.1.30-py3-none-any.whl \
  RELEASE_SHA256=<recorded-wheel-sha256> \
  scripts/gcp/viewer-auth-rollout.sh apply \
  --require-version 0.1.30 --rollback-version 0.1.29 --rehearse-rollback
```

`viewer-auth-rollout.sh` refuses a different project, zone, VM, version,
filename, SHA-256, or dirty/untracked producer. Its release manifest binds the
exact verifier revision and path:
`scripts/gcp/verify_viewer_auth_remote.py`. It first SHA-256-verifies the staged
tracked `viewer-auth-remote.sh` with local SSH logic, installs it, then invokes
that installed producer. `TASCA_VIEWER_MODE=configured` enables the Viewer gate;
omitted, `public`, `clear`, `null`, or `none` leaves Viewer reads public and
does not fetch a Viewer secret.

Before it captures rollback identity, the remote producer initializes and reads
the active 0.1.29 database. Rollback restores the captured unit and environment
byte-for-byte with their captured modes and enabled/disabled state. Preflight
refuses a 0.1.29 baseline containing `TASCA_VIEWER_TOKEN`, preserving public
Viewer reads. The rollback's final device/inode/size/SHA-256 assertion occurs
after the public HTTPS table read, so WAL, schema, or migration writes caused by
that read fail rollback verification.

To restore the captured 0.1.29 state, use the same target and hostname:

```bash
env PROJECT_ID=rda-engineering ZONE=asia-southeast1-b VM=tasca-mcp \
  TASCA_HTTPS_HOST=<approved-hostname> \
  scripts/gcp/viewer-auth-rollout.sh rollback --rollback-version 0.1.29
```
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
     RELEASE_WHEEL=dist/tasca-0.1.30-py3-none-any.whl \
     RELEASE_SHA256=<recorded-wheel-sha256> \
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
