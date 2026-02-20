# Deployment & Ops (v0.1)

> Scope: single-instance local/LAN deployment.

## Configuration

Recommended environment variables:

- `TASCA_DB_PATH` — SQLite file path
- `TASCA_ADMIN_TOKEN` — admin token for privileged UI actions
- `TASCA_HOST` / `TASCA_PORT`
- `TASCA_LOG_LEVEL`

Optional:

- `TASCA_DEDUP_TTL_HOURS_DEFAULT` (fallback if thread policy omits it)
- `TASCA_MESSAGE_MAX_BYTES`

## Backups

- SQLite file backup strategy:
  - stop service OR use SQLite online backup API
  - store backups with timestamps

## Observability

Minimum logs (structured recommended):

- thread.create / thread.update / thread.control
- message.append (include thread_id, cursor, author_kind)
- dedup hits
- wait timeouts vs returns

Minimum metrics (if you add metrics later):

- empty-wait rate
- dedup hit rate
- version conflict rate
- sqlite busy/lock rate
