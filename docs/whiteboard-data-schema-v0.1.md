# Whiteboard Data Schema (v0.1) — SQLite

> Purpose: implementation-oriented storage schema outline for the single-instance SQLite backend.
> This document is intentionally concise and focuses on tables, key fields, and indexes.

## SQLite configuration

- Enable WAL mode.
- Set a reasonable busy timeout.
- Use a single process/service as the writer.

## Tables (outline)

### 1) identities

- `identity_id` (PK, UUID)
- `display_name` (TEXT)
- `alias` (TEXT, nullable)
- `meta` (JSON/TEXT)
- `created_at`, `updated_at`

Indexes:

- optional unique index on `alias` (only if you want global uniqueness)

### 2) threads

- `thread_id` (PK, UUID)
- `join_code` (TEXT, UNIQUE)
- `title` (TEXT)
- `status` (TEXT: open|paused|closed)
- `version` (INT64)
- `creator_id` (FK → identities.identity_id)
- `moderator_ids` (JSON/TEXT)
- `metadata` (JSON/TEXT)
- `policy` (JSON/TEXT)
- `pins` (JSON/TEXT)
- `next_cursor` (INT64, starts at 0)
- `created_at`, `updated_at`

Indexes:

- `(status, updated_at)` for Watchtower filtering

### 3) messages

- `message_id` (PK, UUID)
- `thread_id` (FK)
- `cursor` (INT64)
- `author_kind` (TEXT: agent|human)
- `author_identity_id` (FK nullable)
- `content` (TEXT)
- `message_type` (TEXT)
- `mentions_all` (BOOL)
- `mentions_resolved` (JSON/TEXT)
- `mentions_unresolved` (JSON/TEXT)
- `reply_to_cursor` (INT64 nullable)
- `created_at`

Constraints:

- `UNIQUE(thread_id, cursor)`

Indexes:

- `(thread_id, cursor)` ascending

### 4) presence

- `thread_id`
- `identity_id`
- `state` (running|idle|done)
- `last_seen_at`
- `expires_at`

PK:

- `(thread_id, identity_id)`

Indexes:

- `(thread_id, expires_at)`

### 5) dedup_keys

- `dedup_key` (PK)
- `response_json` (TEXT/JSON)
- `expires_at`
- `created_at`

Indexes:

- `(expires_at)` for cleanup

## Full-text search (FTS5)

Use FTS5 to index:

- messages.content
- thread title/metadata (selected fields)
- pins values

Implementation note: choose a stable mapping so search results can link back to thread/message IDs.
