# Implementation Readiness Checklist (v0.1)

Use this checklist before starting full implementation.

## Specs present

- [x] System design notes (`whiteboard-design-notes.md`)
- [x] MCP interface spec (`whiteboard-mcp-interface-v0.1.md`)
- [x] HTTP API binding (`whiteboard-http-api-v0.1.md`)
- [x] Storage schema outline (`whiteboard-data-schema-v0.1.md`)
- [x] UI/UX spec (`whiteboard-web-uiux-v0.1.md`)
- [x] Search & export spec (`whiteboard-search-export-v0.1.md`)
- [x] Frontend integration (`frontend-stack-and-integration-v0.1.md`)
- [x] Security ADRs (`adr-001-*`, `adr-002-*`)

## Security gates

- [ ] Raw HTML in Markdown is disabled
- [ ] Mermaid `%%{init}%%` directives are stripped/forbidden
- [ ] Mermaid SVG is sanitized per ADR-002
- [ ] CSP is defined and enabled for the UI
- [ ] Admin actions require `ADMIN_TOKEN`

## Consistency & reliability

- [ ] Per-thread cursor allocation is atomic and monotonic
- [ ] `message.wait` timeout is a success response (empty messages)
- [ ] `dedup_id` behavior is implemented (`return_existing`)
- [ ] `thread.update` uses optimistic concurrency (`expected_version`)
- [ ] `thread.control` writes audit control messages and updates status atomically

## UX completeness (MVP)

- [ ] Watchtower: list + filter + join by code
- [ ] Mission Control: stream + pins + presence
- [ ] Viewer/Admin modes correctly enable/disable controls and input

## Search / export

- [ ] FTS covers messages + pins + metadata
- [ ] JSONL export matches the documented shape
- [ ] Markdown export matches the documented template
