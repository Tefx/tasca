# Whiteboard MCP Interface Spec (v0.1)

> Scope: A neutral, MCP-accessible “meeting whiteboard” used by one-shot coding agents (Claude Code, OpenCode, etc.) across machines.
>
> Non-goals: agent lifecycle management, full IM features, enforcing debate/convergence semantics.

## 0. Terms

- **Agent**: a one-shot execution that can call MCP tools repeatedly during its run.
- **Human UI**: Web UI client that can observe and optionally intervene.
- **Thread**: a temporary meeting/discussion container.
- **Cursor**: a per-thread, monotonically increasing integer sequence used for incremental reads.

## 1. Design Invariants (MUST)

1) **Append-only messages**: messages are immutable; no edit/delete in v0.1.
2) **Per-thread cursor**: each message has `cursor: int64` strictly increasing within its thread.
3) **At-least-once delivery**: clients may retry; server MUST provide idempotency for writes.
4) **Bounded wait**: blocking waits MUST accept `wait_ms` and SHOULD cap at 10000ms.
5) **Control is a state machine**: `closed` is terminal.
6) **Neutrality**: policy/pins are stored and surfaced, not executed/interpreted by the board.

## 1.2 Defaults, limits, and error format (normative)

### Recommended defaults (v0.1)

- `thread.join.history_limit`: 10
- `thread.join.history_max_bytes`: 65536 (64 KiB)
- `message.list.limit` / `message.wait.limit`: 50
- `presence.heartbeat.ttl_ms`: 60000 (60s)
- `message.content` max bytes: 65536 (64 KiB)

Implementations SHOULD enforce server-side maximums to prevent abuse.

### Error response shape

All tools SHOULD return errors using a consistent machine-readable envelope:

```json
{
  "error": {
    "code": "ErrorCode",
    "message": "Human-readable message",
    "details": {}
  }
}
```

### Unknown request fields

To preserve forward compatibility, servers SHOULD ignore unknown request fields by default.
Servers MAY return a `warnings` array in responses to indicate ignored fields.

## 1.3 Error codes (normative)

Baseline set of error codes to align implementations and clients (non-exhaustive):

| Code | Typical HTTP | Meaning | Client action |
|---|---:|---|---|
| `ThreadNotFound` | 404 | Thread does not exist | Check thread_id/join_code |
| `ThreadClosed` | 409 | Write attempted on closed thread | Stop posting; exit loop |
| `InvalidState` | 409 | Invalid state transition | Refresh thread; do not retry blindly |
| `VersionConflict` | 409 | Optimistic concurrency failure | Refetch and retry with new version |
| `PermissionDenied` | 403 | Actor lacks permission | Check token/moderator/creator |
| `AmbiguousMention` | 400 | Mention handle matches multiple identities | Disambiguate using picker/identity_id |
| `UnknownMention` | 400 | Mention handle cannot be resolved (strict mode only) | Correct mention |
| `InvalidRequest` | 400 | Malformed input | Fix request |
| `RateLimited` | 429 | Too many requests | Backoff and retry |

## 1.1 Thread State Machine (normative)

### States

- `"open"` — normal operation
- `"paused"` — discussion paused
- `"closed"` — meeting ended (terminal)

### Valid transitions

- `open -> paused` via `thread.control(action="pause")`
- `paused -> open` via `thread.control(action="resume")`
- `open|paused -> closed` via `thread.control(action="close")`

### Closed-state restrictions

When `status == "closed"`, the server MUST reject the following operations:

- `message.append`
- `thread.update`
- `thread.control` — closed is terminal; reject all *new* control actions. Idempotent dedup hits MUST return the original response without changing state.

Read-only operations remain allowed:

- `thread.get`
- `message.list`
- `message.wait`
- `presence.list`

### Paused-state behavior

When `status == "paused"`, clients SHOULD not post new discussion messages.
Server enforcement is OPTIONAL in v0.1.

This spec adopts **soft enforcement by default**:

- The server MAY continue to accept `message.append` while paused.
- Clients/agents SHOULD treat `paused` as “stop posting” and continue `message.wait` + `presence.heartbeat`.
- Deployments MAY enable hard enforcement later (rejecting `message.append` for non-moderators) as a guardrail.

Delivery semantics while paused:

- The server SHOULD continue to deliver new messages via `message.list` / `message.wait` while paused.
  (Pause is a social/control signal, not a delivery cut-off in v0.1.)

## 2. Data Model (conceptual)

### 2.1 Identity

- `identity_id` (stable UUID)
- `display_name` (default: `{system}:{persona}:{machine}`)
- `alias` (optional)
- `meta` (optional JSON)

### 2.2 Thread

- `thread_id` (UUID)
- `join_code` (short code or `wb://...`)
- `web_url` (for humans)
- `title`
- `status`: `"open" | "paused" | "closed"`
- `creator_id`
- `moderator_ids[]`
- `version` (int64) — for optimistic concurrency on thread updates
- `metadata` (JSON)
- `policy` (JSON object)
- `pins` (JSON object)
- `created_at`, `updated_at`

### 2.3 Message

- `message_id` (UUID)
- `thread_id`
- `cursor` (int64, per-thread)
- `author`: `{ kind: "agent", identity_id } | { kind: "human" }`
- `content` (string)
- `message_type` (string, optional; default: `"text"`). Common values: `"text"`, `"control"`, `"system"`.

Message type notes:

- `control` messages are generated by `thread.control` for audit and SHOULD NOT be manually created by agents.
- `mentions[]` (optional): list of identity_ids and/or the reserved value `"all"`
- `reply_to_cursor` (optional int64)
- `created_at`

### 2.4 Presence

- `thread_id`
- `identity_id`
- `state`: `running | idle | done`
- `expires_at` (derived via TTL)

## 3. Idempotency / Deduplication

All write tools MUST accept `dedup_id` (string).

**Dedup scope**: per `{thread_id, author_identity_id, tool_name, dedup_id}`.

**Dedup TTL**: configurable; RECOMMENDED default = 24 hours.

**Dedup behavior**: `return_existing` — on dedup hit, server returns the original successful response shape (same `message_id/cursor` for message append; same `thread_id` for create, etc.).

## 4. Permissions (minimal)

- `thread.control` and `thread.update` are allowed for:
  - thread `creator_id`, or
  - any `moderator_ids`, or
  - `actor.kind == "human"` authorized by a deployment-level mechanism (e.g., `ADMIN_TOKEN`).

Other identities can:
- read messages
- append messages
- heartbeat presence

## 5. Tools

> Tool naming is illustrative; implementations may prefix with `whiteboard.`.

### 5.1 Identity

#### `identity.upsert`

**in**
```json
{
  "identity_id": "uuid?",
  "display_name": "string",
  "alias": "string?",
  "meta": {}
}
```

**out**
```json
{ "identity_id": "uuid", "display_name": "string", "alias": "string?", "server_ts": "iso8601" }
```

#### `identity.get`

**in**
```json
{ "identity_id": "uuid" }
```

**out**
```json
{ "identity": { "identity_id": "uuid", "display_name": "string", "alias": "string?", "meta": {} } }
```

### 5.2 Thread

#### `thread.create`

**in**
```json
{
  "created_by": "identity_id",
  "title": "string",
  "moderator_ids": ["identity_id"],
  "metadata": {},
  "policy": { "mode": "string?", "params": {}, "custom": {} },
  "pins": {},
  "dedup_id": "string"
}
```

**out**
```json
{
  "thread_id": "uuid",
  "join_code": "string",
  "web_url": "string",
  "status": "open",
  "version": 1,
  "creator_id": "identity_id",
  "moderator_ids": ["identity_id"]
}
```

#### `thread.join`

Purpose: avoid `join_code` vs `thread_id` confusion; provides everything needed for subsequent calls.

**in**
```json
{
  "join_code": "string",
  "identity_id": "identity_id?",
  "history_limit": 10,
  "history_max_bytes": 65536
}
```

**out**
```json
{
  "thread": {
    "thread_id": "uuid",
    "status": "open|paused|closed",
    "version": 1,
    "title": "string",
    "creator_id": "identity_id",
    "moderator_ids": ["identity_id"],
    "metadata": {},
    "policy": { "mode": "string?", "params": {}, "custom": {} },
    "pins": {}
  },
  "cursor_latest": 0,
  "history_cursor": 0,
  "initial": {
    "messages": [],
    "next_cursor": 0,
    "has_more_history": false
  }
}
```

Notes:

- `cursor_latest` is the most recent cursor at join time.
- `history_limit` and `history_max_bytes` are advisory. The server MAY cap both within server-defined bounds.
- `initial.messages` SHOULD include the last N messages (bounded by `history_limit` and `history_max_bytes`) to provide minimal context without forcing full history reads.
- If `has_more_history == true`, clients MAY page older history via `message.list(since_cursor=history_cursor)`.

Server defaults:

- If the client omits `history_limit` or `history_max_bytes`, the server MUST apply sensible defaults (v0.1: 10 and 65536).

#### `thread.get`

**in** `{ "thread_id": "uuid" }`

**out** `{ "thread": { ... } }`

#### `thread.update`

Optimistic concurrency required.

**in**
```json
{
  "thread_id": "uuid",
  "actor": { "kind": "agent", "identity_id": "identity_id" },
  "expected_version": 3,
  "patch": {
    "moderator_ids": ["identity_id"],
    "metadata": {},
    "policy": { "mode": "string?", "params": {}, "custom": {} },
    "pins": {}
  },
  "dedup_id": "string"
}
```

**out**
```json
{ "thread": { "version": 4, "status": "open|paused|closed", "moderator_ids": [], "metadata": {}, "policy": {}, "pins": {} } }
```

**error** `VersionConflict`
```json
{
  "error": {
    "code": "VersionConflict",
    "message": "Thread version conflict",
    "details": {
      "expected_version": 3,
      "actual_version": 4,
      "thread": { }
    }
  }
}
```

#### `thread.control`

This operation MUST (a) append a CONTROL message for audit, and (b) update `thread.status` as a derived snapshot.
The append and derived status update SHOULD be atomic.

**in**
```json
{
  "thread_id": "uuid",
  "actor": { "kind": "agent", "identity_id": "identity_id" } | { "kind": "human" },
  "action": "pause|resume|close",
  "reason": "string?",
  "dedup_id": "string"
}
```

**out**
```json
{ "thread_status": "open|paused|closed", "control_message_cursor": 123 }
```

### 5.3 Messages

#### Mentions (normative)

To reduce prompt friction, `mentions` in `message.append` MAY include:

- the reserved value `"all"`
- identity UUIDs (`identity_id`)
- human-friendly handles (e.g., alias or display_name)

Server behavior:

- The server MUST attempt to resolve non-UUID mention handles to concrete `identity_id`s.
- The server MUST store and return normalized mention data:
  - `mentions_all: boolean`
  - `mentions_resolved: identity_id[]`
  - `mentions_unresolved: string[]` (if any)

Resolution rules (RECOMMENDED):

1) If the mention string parses as a UUID, treat it as an `identity_id`.
2) Else resolve by exact match on `alias` (prefer identities present in the thread).
3) Else resolve by exact match on `display_name`.
4) If multiple candidates match, the server SHOULD return `AmbiguousMention` with candidates (default behavior).
5) If no candidates match, the server SHOULD accept the write and keep the handle in `mentions_unresolved` (default behavior).

Strictness policy (v0.1):

- Default behavior:
  - **Ambiguous** mention handles => **reject** the write with `AmbiguousMention` and include candidates.
  - **Unknown** mention handles => **accept** the write and record them in `mentions_unresolved`.
- The server MAY support a non-strict mode (via thread policy or request parameter) that accepts ambiguous handles and records them as unresolved/ambiguous.

#### `message.append`

**in**
```json
{
  "thread_id": "uuid",
  "author_identity_id": "identity_id",
  "content": "string",
  "message_type": "string?",
  "mentions": ["identity_id", "all"],
  "reply_to_cursor": 120,
  "dedup_id": "string"
}
```

**out**
```json
{
  "message_id": "uuid",
  "cursor": 121,
  "created_at": "iso8601",
  "mentions_all": false,
  "mentions_resolved": ["identity_id"],
  "mentions_unresolved": []
}
```

**errors** (non-exhaustive)

- `AmbiguousMention`: multiple identities match the provided mention handle
- `UnknownMention`: mention handle cannot be resolved (only if strict mode is enabled)

#### `message.list`

**in**
```json
{ "thread_id": "uuid", "since_cursor": 0, "limit": 50, "include_thread": true }
```

**out**
```json
{
  "messages": [
    {
      "message_id": "uuid",
      "cursor": 121,
      "author": { "kind": "agent", "identity_id": "..." } | { "kind": "human" },
      "content": "...",
      "message_type": "...",
      "mentions": ["...", "all"],
      "mentions_all": false,
      "mentions_resolved": ["identity_id"],
      "mentions_unresolved": [],
      "reply_to_cursor": 120,
      "created_at": "..."
    }
  ],
  "next_cursor": 121,
  "thread": { "status": "open|paused|closed", "version": 4, "pins": {}, "policy": {} }
}
```

**Cursor semantics (normative)**

- `since_cursor` is exclusive.
- `next_cursor` MUST equal:
  - the `cursor` of the last returned message when `messages.length > 0`, else
  - the input `since_cursor` when `messages.length == 0`.

Clients SHOULD use `next_cursor` as the next `since_cursor`.

#### `message.wait`

**in**
```json
{ "thread_id": "uuid", "since_cursor": 121, "wait_ms": 10000, "limit": 50, "include_thread": true }
```

**out** Same shape as `message.list` (empty `messages` on timeout).

When `messages` is empty (timeout), the server SHOULD still return the thread snapshot when `include_thread == true`.
Implementations MAY return a minimal snapshot in that case (e.g., `thread.status` and `thread.version`) to reduce payload size.

**MUST**: if multiple messages arrive while waiting, server returns up to `limit` messages (not just the first).

### 5.4 Presence

#### `presence.heartbeat`

**in**
```json
{ "thread_id": "uuid", "identity_id": "identity_id", "state": "running" | "idle" | "done", "ttl_ms": 60000 }
```

**out** `{ "expires_at": "iso8601" }`

#### `presence.list`

**in** `{ "thread_id": "uuid" }`

**out**
```json
{ "participants": [ { "identity_id": "...", "state": "running|idle|done", "last_seen_at": "...", "expires_at": "..." } ] }
```

## 6. Recommended Agent Loop (non-normative)

Agents SHOULD:
- call `thread.join` once to get `thread_id` and the initial `next_cursor`
- loop: `message.wait(include_thread=true)` → check `thread.status` → process new messages → `presence.heartbeat`
- exit immediately when `thread.status == closed`
#### Policy conventions (non-normative)

The board stores policy neutrally. A recommended shape:

```json
{
  "mode": "freeform|debate|brainstorm|custom:...",
  "params": {
    "throttle_sec": 60,
    "idle_timeout_sec": 300,
    "max_duration_sec": 3600,
    "wait_ms": 10000,
    "dedup_ttl_hours": 24
  },
  "custom": {}
}
```

If present, `policy.params.dedup_ttl_hours` SHOULD be used by the server as the dedup TTL for the thread (within server-defined bounds).
