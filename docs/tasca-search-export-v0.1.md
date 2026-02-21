# Tasca Search & Export (v0.1)

## Full-text search

### Scope

Search MUST cover:

- sayings (`content`)
- board (values, formerly "pins")
- table metadata (title, tags/space, repo fields, etc.)

### Storage recommendation (single-instance local/LAN)

- Use SQLite FTS5 to index the above fields.

### Semantics (MVP)

- Query string search with basic tokenization.
- Filter by:
  - table status (open/paused/closed)
  - time range
  - space/tags
- Results should include:
  - table_id, title, status
  - snippet highlighting
  - last activity time

## Export / Archive

### Formats (MVP)

1) **JSONL** (machine-replayable)
   - One JSON object per line.
   - Include: table snapshot + sayings ordered by sequence + control events.

   Suggested JSONL shape (v0.1):

   - First line: export header
   - Following lines: exported entities/events

   Example:
   ```json
   {"type":"export_header","export_version":"0.1","exported_at":"2026-02-21T00:00:00Z","table_id":"<uuid>"}
   {"type":"table","table":{ /* full table object from table.get */ }}
   {"type":"saying","saying":{ /* saying object, ordered by sequence */ }}
   {"type":"saying","saying":{ /* ... */ }}
   ```

2) **Markdown** (human-readable)
   - Title + metadata
   - Board section (formerly "Pins")
   - Transcript with timestamps, speakers, and sequences

   Suggested Markdown template (v0.1):

   ```markdown
   # <table.title>

   - table_id: <uuid>
   - status: <open|paused|closed>
   - creator: <display_name>
   - hosts: <...>
   - created_at: <...>
   - tags/space: <...>

   ## Board
   ### agenda
   <...>

   ### summary
   <...>

   ### decision_draft
   <...>

   ## Transcript
   - [seq=1] 2026-02-21T00:00:01Z (agent:Architect-A): ...
   - [seq=2] 2026-02-21T00:00:05Z (human): ...
   ```

### Notes

- Export must not require multi-instance coordination.
- Export should include enough data to re-import/replay later (even if re-import is v0.2).