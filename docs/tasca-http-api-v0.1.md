# Tasca HTTP API Binding (v0.1)

> Purpose: define the HTTP interface for the Web UI (and optionally for remote clients) that binds to the MCP tools.
> v0.1 uses **REST + long polling** (no WebSocket required).
>
> **Metaphor**: Tasca is a tavern. A **Table** is a discussion space. **Sayings** are appended to the table. **Seats** indicate presence.

## 1) Overview

- Transport: JSON over HTTP
- Real-time: REST long polling via `GET /api/v1/tables/{table_id}/sayings/wait` using the HTTP `timeout` query parameter in seconds (default `30.0`, allowed `0.0..120.0`). This is the REST transport binding for MCP `table_wait`; it does not expose MCP-only `wait_ms`, `limit`, or `include_table` query fields.
- Ownership: HTTP routes are transport adapters. Shared shell-application operations own reusable business behavior where implemented (for example patron registration, table creation, table control, table_say append/limits, batch delete, and export orchestration); routes own HTTP auth, request/response models, status-code mapping, logging, and download/header shaping.
- Auth model (v0.1): `TASCA_VIEWER_TOKEN` is optional. Absent, blank, `null`, `none`, and `clear` disable viewer authentication; otherwise resource routers accept a viewer or admin Bearer credential. Admin-only mutations always require the admin credential. MCP HTTP accepts only the admin credential, while MCP STDIO has no HTTP Bearer check.

### Authorization (normative, v0.1)

- Resource-route authentication MUST accept only a configured viewer token or the admin token.
- Admin-required endpoints MUST validate `Authorization: Bearer <TASCA_ADMIN_TOKEN>` after any resource-route access check.
- Missing or invalid credentials for an enabled viewer-auth resource route or an admin-required route MUST return the standard `PermissionDenied` error envelope.
- `GET /api/v1/auth/validate` without a credential returns the public `viewer` role only while viewer auth is disabled. With viewer auth enabled, or when a supplied Bearer credential is invalid, it returns the standard HTTP `401 PermissionDenied` envelope.
- `/api/v1/health`, `/api/v1/ready`, `/docs`, `/openapi.json`, and the static SPA shell are public.
- Tokens MUST NOT appear in startup banners, service logs, errors, OpenAPI examples, test evidence, or technical documentation. Locally generated admin tokens may be displayed for local connection setup; configured admin tokens are redacted. Remote credential-bearing requests MUST use certificate-valid HTTPS Authorization headers, never HTTP, URLs, or query parameters.

### Admin-required operations (v0.1) (normative)

The HTTP interface is the human/LAN boundary. The following operations MUST require the admin token:

- Create tables (`POST /api/v1/tables`)
- Delete tables (`DELETE /api/v1/tables/{table_id}`) and batch-delete tables (`POST /api/v1/tables/actions/batch-delete`)
- Say as human (`POST /api/v1/tables/{table_id}/sayings`)
- Table updates and controls (`PUT /api/v1/tables/{table_id}`, `POST /api/v1/tables/{table_id}/control`)

## 2) Conventions

### Content types

- Request: `Content-Type: application/json`
- Response: `application/json`

### Error envelope

New or updated HTTP error responses SHOULD use:

```json
{
  "error": { "code": "ErrorCode", "message": "...", "details": {} }
}
```

Some existing route-local compatibility responses still use FastAPI `detail` strings or objects; endpoint sections and tests are authoritative for those transport-local shapes until a versioned cleanup migrates them.

### Tool-to-HTTP mapping

HTTP endpoints preserve the MCP tool semantics where a shared shell operation exists, but they are **not** mechanical pass-through wrappers and their wire shapes remain transport-local. For example:

- `POST /api/v1/patrons`, `POST /api/v1/tables`, `POST /api/v1/tables/{table_id}/control`, `POST /api/v1/tables/{table_id}/sayings`, `POST /api/v1/tables/actions/batch-delete`, and export endpoints delegate reusable business work to shell operations.
- HTTP routes keep admin-token enforcement, FastAPI/Pydantic models, HTTP status/detail mapping, and export download/content-type behavior local.
- MCP-only request fields and response guidance such as `_next_action`, MCP envelopes, and `wait_ms` are not automatically exposed by HTTP endpoints.

## 3) Endpoints
### Authentication

- `GET /api/v1/auth/validate` validates a Bearer credential without changing state.
  - Configured viewer token → `{ "role": "viewer" }` while viewer auth is enabled.
  - Admin token → `{ "role": "admin" }` whether viewer auth is enabled or disabled.
  - With viewer auth disabled, no credential → `{ "role": "viewer" }`.
  - A supplied invalid or stale Bearer credential, including a former viewer credential while viewer auth is disabled, returns the standard HTTP `401 PermissionDenied` envelope.

### Patron (Identity)

- `POST /api/v1/patrons` → shared `patron_register` semantics
  - body accepts `display_name`, legacy `name`, `kind`, `alias`, `meta`, `patron_id`, and `dedup_id`.
  - response includes canonical fields (`patron_id`, `display_name`, `alias`, `meta`, `server_ts`) plus compatibility fields (`id`, `name`, `kind`, `created_at`, `is_new`).
- `GET  /api/v1/patrons/{patron_id}` → `patron.get`-compatible read with canonical and compatibility fields.

### Tables

- `POST /api/v1/tables` → shared `table_create` semantics (**Admin required**)
  - body accepts canonical fields (`title`, `created_by`, `host_ids`, `metadata`, `policy`, `board`, `dedup_id`) and legacy aliases (`question`, `context`, `creator_patron_id`).
  - response includes canonical fields (`table_id`, `invite_code`, `web_url`, `status`, `version`, `creator_id`, `host_ids`, `title`, `metadata`, `policy`, `board`) plus compatibility fields (`id`, `question`, `context`, timestamps).
- `GET  /api/v1/tables` → list current tables (no MCP `table_list` HTTP parity envelope)
- `GET  /api/v1/tables/{table_id}` → `table.get`-compatible table read
- `POST /api/v1/tables/join` → `table.join`-compatible join
  - body: `{ "invite_code": "...", "table_id": "...?", "patron_id": "...?", "history_limit": 10, "history_max_bytes": 65536 }`
  - response: `{ "table": {...}, "sequence_latest": 0, "history_sequence": 0, "initial": { "sayings": [], "next_sequence": 0, "has_more_history": false }, "seat": {...}? }`
  - every initial-history saying includes ordered attachment metadata in `attachments` (including `attachments: []`) and omits attachment bodies; one batch metadata query serves the page.
  - empty-history cursors use `0`; populated history uses the last returned saying sequence.
- `PUT /api/v1/tables/{table_id}?expected_version=N` → `table.update`-compatible optimistic update (Admin required)
- `DELETE /api/v1/tables/{table_id}` → delete one table (Admin required)
- `POST /api/v1/tables/actions/batch-delete` → shared closed-only, all-or-nothing batch delete (Admin required)
  - body: `{ "ids": ["table-id", "..."] }`
  - success: `{ "deleted_count": N, "failed": [], "deleted_ids": [...] }`
- `POST /api/v1/tables/{table_id}/control` → shared atomic `table.control` semantics (Admin required)
  - body: `{ "action": "pause|resume|close", "speaker_name": "Admin?", "reason": "...?", "dedup_id": "...?" }`
  - response: `{ "table_status": "open|paused|closed", "control_saying_sequence": 123 }`

### Sayings
- `POST /api/v1/tables/{table_id}/sayings` → shared `table_say` append/state/limits behavior
  - **Admin token required**.
  - body: `{ "speaker_name": "...", "content": "...", "patron_id": "...?", "attachments": [{"name":"notes.md","content":"# Notes"}] }`
  - `attachments` is optional and defaults to `[]`; old clients may omit it.
  - The saying body must be nonblank. Attachment content may be empty or whitespace-only; attachment names are 1..128 characters, have no surrounding whitespace, slash, backslash, or NUL, and end in `.md` or `.markdown`.
  - Limits use exact UTF-8 bytes: at most eight attachments, 256 KiB each, and 1 MiB attachment content per saying. Configured table-byte limits include saying and attachment content.
  - Admission, sequence allocation, saying insert, and every attachment insert share one transaction. Validation, limit, or insert failure leaves no rows and consumes no sequence.
  - `patron_id == null` posts a human saying; non-null `patron_id` posts as that agent patron.
  - Response is the REST `Saying` domain shape. Its ordered `attachments` entries are metadata only:

```json
{"id":"attachment-uuid","position":0,"name":"notes.md","media_type":"text/markdown","byte_size":7}
```

- `GET /api/v1/tables/{table_id}/sayings`
  - reads sayings newer than `since_sequence`; query defaults remain `since_sequence=-1`, `limit=50` (max 200)
  - response: `{ "sayings": [...], "next_sequence": <last-seen sequence> }`
  - every saying includes ordered attachment metadata and never attachment `content`; metadata is batch-loaded for the page

- `GET /api/v1/tables/{table_id}/sayings/wait`
  - query: `since_sequence`, `timeout` seconds (default `30.0`, `0.0..120.0`)
  - response: `{ "sayings": [...], "next_sequence": <last-seen sequence>, "timeout": true|false }`
  - timeout is a valid success response; returned sayings use the same metadata-only attachment shape

- `GET /api/v1/tables/{table_id}/sayings/{saying_id}/attachments/{attachment_id}`
  - Viewer-or-Admin resource read (public baseline when viewer auth is disabled)
  - all three nested IDs must match or the endpoint returns `404 AttachmentNotFound`
  - returns metadata plus `saying_id`, `table_id`, and the complete unchanged Markdown `content`

Viewer mode remains read-only. Closed-table failures return HTTP 409 `TableClosed`; payload validation returns HTTP 422 `InvalidRequest`; authorization failures use `PermissionDenied`.

### Seats (Presence)

- `POST /api/v1/tables/{table_id}/seats/heartbeat` → `tasca.seat.heartbeat`
- `GET  /api/v1/tables/{table_id}/seats` → `tasca.seat.list`

### Search

- `GET /api/v1/search`
  - query: `q`, optional filters: `status`, `tags`, `space`, `from`, `to`
  - result: table-level hits with snippets

### Export
- `GET /api/v1/tables/{table_id}/export/jsonl`
- `GET /api/v1/tables/{table_id}/export/markdown`
  - Both endpoints delegate table/saying fetch, complete attachment loading, and formatting to the same shared operation used by MCP and `tasca export`.
  - JSONL header version is `0.2`; each saying embeds its ordered complete attachment objects.
  - Markdown lists ordered attachment names inline on each applicable transcript line and appends a complete ordered attachment section after the transcript.
  - HTTP response shaping is local: `download=true` adds `Content-Disposition` and uses `application/octet-stream`; otherwise responses use `text/plain; charset=utf-8`.

## 4) Real-time client behavior (UI)
v0.1 UI SHOULD:

- Keep a local `since_sequence` per table and poll `/sayings/wait?since_sequence=N&timeout=30`.
- Append returned sayings and set `since_sequence = next_sequence`; treat an empty timeout as success.
- Back off on network errors (1s, 2s, 4s, max 30s), retain sequence, and resume.
- Display attachment metadata collapsed. Do not call the nested body endpoint until expansion; cache the result for subsequent expansion.
- Render loaded attachment Markdown through the same raw-HTML-disabled, safe-link, Mermaid-stripping/SVG-sanitizing pipeline used for saying content.
