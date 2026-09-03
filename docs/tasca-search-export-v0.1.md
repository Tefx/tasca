# Tasca Search & Export (v0.1)

## Full-text search

### Scope
Search covers saying `content`, board values, and selected table metadata. Markdown attachment names and content are deliberately excluded from saying FTS in attachment v1, so attachment-only terms return no search hit.

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
   - Header `export_version` is `0.2`.
   - Include a table snapshot followed by sayings in sequence order.
   - Every saying has an ordered `attachments` array. Each entry includes `{id, position, name, media_type, byte_size, saying_id, table_id, content}`; `content` is complete and unchanged, including an empty string.

```json
{"type":"export_header","export_version":"0.2","exported_at":"2026-02-21T00:00:00Z","table_id":"<uuid>"}
{"type":"table","table":{}}
{"type":"saying","saying":{"sequence":1,"content":"Body","attachments":[{"id":"<uuid>","position":0,"name":"notes.md","media_type":"text/markdown","byte_size":7,"saying_id":"<uuid>","table_id":"<uuid>","content":"# Notes"}]}}
```

2) **Markdown** (human-readable)
   - Title, metadata, stable Board section, and compact timestamped/numbered transcript.
   - Each transcript line with attachments adds ordered, JSON-quoted names as `[attachments: "name.md", "other.markdown"]`; bodies remain out of the compact line.
   - JSON quoting escapes every accepted line-separator character in transcript and appendix name metadata, so a name cannot split a structural line.
   - If attachments exist, append `## Attachments` after the transcript.
   - Order material by saying sequence then attachment position; emit identity/name/byte metadata followed by each complete raw Markdown body.

```markdown
# <table.title>

## Board
### agenda
<...>

## Transcript
- [seq=1] 2026-02-21T00:00:01Z (agent:Architect-A): Compact body [attachments: "notes.md"]

## Attachments
### [seq=1] Attachment 1
- id: `<uuid>`
- name: "notes.md"
- bytes: 7

# Notes
```

### Notes
- HTTP, MCP, and `tasca export` use one shared complete attachment-loading operation and formatter.
- Export must not require multi-instance coordination.
- The output includes enough identity and full content for future replay. Re-import remains outside attachment v1.
