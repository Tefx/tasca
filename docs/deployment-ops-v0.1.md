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

- `TASCA_DB_PATH` — SQLite file path
- `TASCA_ADMIN_TOKEN` — admin token for privileged UI actions
- `TASCA_HOST` / `TASCA_PORT`
- `TASCA_LOG_LEVEL`

Optional:

- `TASCA_DEDUP_TTL_HOURS_DEFAULT` (fallback if table policy omits it)
- `TASCA_SAYING_MAX_BYTES`

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