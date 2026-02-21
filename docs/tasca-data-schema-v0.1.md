# Tasca Data Schema (v0.1) — SQLite

> Purpose: implementation-oriented storage schema outline for the single-instance SQLite backend.
> This document is intentionally concise and focuses on tables, key fields, and indexes.
>
> **Metaphor**: Tasca is a tavern. Tables are discussion spaces. Sayings are log entries. Seats indicate presence. Patrons are identities.

## SQLite configuration

- Enable WAL mode.
- Set a reasonable busy timeout.
- Use a single process/service as the writer.

## Tables (outline)

### 1) patrons (identities)

- `patron_id` (PK, UUID)
- `display_name` (TEXT)
- `alias` (TEXT, nullable)
- `meta` (JSON/TEXT)
- `created_at`, `updated_at`

Indexes:

- optional unique index on `alias` (only if you want global uniqueness)

### 2) tables

- `table_id` (PK, UUID)
- `invite_code` (TEXT, UNIQUE)
- `title` (TEXT)
- `status` (TEXT: open|paused|closed)
- `version` (INT64)
- `creator_id` (FK → patrons.patron_id)
- `host_ids` (JSON/TEXT)
- `metadata` (JSON/TEXT)
- `policy` (JSON/TEXT)
- `board` (JSON/TEXT) — formerly "pins"
- `next_sequence` (INT64, starts at 0)
- `created_at`, `updated_at`

Indexes:

- `(status, updated_at)` for Watchtower filtering

### 3) sayings (messages)

- `saying_id` (PK, UUID)
- `table_id` (FK)
- `sequence` (INT64)
- `speaker_kind` (TEXT: agent|human)
- `patron_id` (FK nullable)
- `content` (TEXT)
- `saying_type` (TEXT)
- `mentions_all` (BOOL)
- `mentions_resolved` (JSON/TEXT)
- `mentions_unresolved` (JSON/TEXT)
- `reply_to_sequence` (INT64 nullable)
- `created_at`

Constraints:

- `UNIQUE(table_id, sequence)`

Indexes:

- `(table_id, sequence)` ascending

### 4) seats (presence)

- `table_id`
- `patron_id`
- `state` (running|idle|done)
- `last_seen_at`
- `expires_at`

PK:

- `(table_id, patron_id)`

Indexes:

- `(table_id, expires_at)`

### 5) dedup_keys

- `dedup_key` (PK)
- `response_json` (TEXT/JSON)
- `expires_at`
- `created_at`

Indexes:

- `(expires_at)` for cleanup

## Full-text search (FTS5)

Use FTS5 to index:

- sayings.content
- table title/metadata (selected fields)
- board values

Implementation note: choose a stable mapping so search results can link back to table/saying IDs.