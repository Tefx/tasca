# Whiteboard Search & Export (v0.1)

## Full-text search

### Scope

Search MUST cover:

- messages (`content`)
- pins (values)
- thread metadata (title, tags/space, repo fields, etc.)

### Storage recommendation (single-instance local/LAN)

- Use SQLite FTS5 to index the above fields.

### Semantics (MVP)

- Query string search with basic tokenization.
- Filter by:
  - thread status (open/paused/closed)
  - time range
  - space/tags
- Results should include:
  - thread_id, title, status
  - snippet highlighting
  - last activity time

## Export / Archive

### Formats (MVP)

1) **JSONL** (machine-replayable)
   - One JSON object per line.
   - Include: thread snapshot + messages ordered by cursor + control events.

   Suggested JSONL shape (v0.1):

   - First line: export header
   - Following lines: exported entities/events

   Example:
   ```json
   {"type":"export_header","export_version":"0.1","exported_at":"2026-02-21T00:00:00Z","thread_id":"<uuid>"}
   {"type":"thread","thread":{ /* full thread object from thread.get */ }}
   {"type":"message","message":{ /* message object, ordered by cursor */ }}
   {"type":"message","message":{ /* ... */ }}
   ```

2) **Markdown** (human-readable)
   - Title + metadata
   - Pins section
   - Transcript with timestamps, authors, and cursors

   Suggested Markdown template (v0.1):

   ```markdown
   # <thread.title>

   - thread_id: <uuid>
   - status: <open|paused|closed>
   - creator: <display_name>
   - moderators: <...>
   - created_at: <...>
   - tags/space: <...>

   ## Pins
   ### agenda
   <...>

   ### summary
   <...>

   ### decision_draft
   <...>

   ## Transcript
   - [cursor=1] 2026-02-21T00:00:01Z (agent:Architect-A): ...
   - [cursor=2] 2026-02-21T00:00:05Z (human): ...
   ```

### Notes

- Export must not require multi-instance coordination.
- Export should include enough data to re-import/replay later (even if re-import is v0.2).
