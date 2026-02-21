# Deployment & Ops (v0.1)

> Scope: single-instance local/LAN deployment.
>
> **Metaphor**: Tasca is a tavern. The service manages Tables, Sayings, and Seats.

## v0.1 deployment constraint (normative)

**SQLite writer model (MUST):** deploy as a **single API service process** writing to the SQLite DB.

- Do **not** run multiple backend processes/workers against the same `TASCA_DB_PATH` in v0.1.
- A reverse proxy is fine; the constraint is about concurrent writers to the DB file.

Rationale: v0.1 correctness relies on atomic sequence allocation, idempotency, and optimistic concurrency within a single writer's transaction boundaries.
Multi-process support requires a fresh concurrency design review (out of scope for v0.1).

## Configuration

Recommended environment variables:

- `TASCA_DB_PATH` — SQLite file path (**use absolute path** when running multiple processes)
- `TASCA_ADMIN_TOKEN` — admin token for privileged actions (auto-generated if unset, see below)
- `TASCA_API_HOST` / `TASCA_API_PORT` — server bind address (default `0.0.0.0:8000`)
- `TASCA_ENVIRONMENT` — `development` or `production` (controls CSP strictness)
- `TASCA_DEBUG` — enable verbose logging

Optional:

- `TASCA_CORS_ORIGINS` — JSON list of allowed origins (e.g., `["http://localhost:5173"]`)
- `TASCA_CSP_ENABLED` / `TASCA_CSP_REPORT_ONLY` — Content Security Policy controls
- `TASCA_MAX_SAYINGS_PER_TABLE` — cap sayings per table
- `TASCA_MAX_CONTENT_LENGTH` — cap characters per saying
- `TASCA_MAX_BYTES_PER_TABLE` — cap total data per table
- `TASCA_MAX_MENTIONS_PER_SAYING` — cap @mentions per saying

All variables use the `TASCA_` prefix. See `.env.example` for a commented template.

### Admin token behavior

- If `TASCA_ADMIN_TOKEN` is set: use the provided value.
- If unset: auto-generate a `tk_`-prefixed token and print to stderr on startup.
- The auto-generated token is ephemeral (lives only as long as the server process).
- Agents receive the token via conversation (human tells agent), not via config files.
- See `tasca-interaction-design-v0.2.md` §4 for the full token flow.

## Backups

- SQLite file backup strategy:
  - stop service OR use SQLite online backup API
  - store backups with timestamps

## Observability

Minimum logs (structured recommended):

- table.create / table.update / table.control
- table.say (include table_id, sequence, speaker_kind)
- dedup hits
- wait timeouts vs returns

Minimum metrics (if you add metrics later):

- empty-wait rate
- dedup hit rate
- version conflict rate
- sqlite busy/lock rate