# Implementation Readiness Checklist (v0.1)

Use this checklist before starting full implementation.

**Metaphor**: Tasca is a tavern. Tables are discussion spaces. Sayings are log entries. Seats show presence.

## Specs present

- [x] System design notes (`tasca-design-notes.md`)
- [x] MCP interface spec (`tasca-mcp-interface-v0.1.md`)
- [x] HTTP API binding (`tasca-http-api-v0.1.md`)
- [x] Storage schema outline (`tasca-data-schema-v0.1.md`)
- [x] UI/UX spec (`tasca-web-uiux-v0.1.md`)
- [x] Search & export spec (`tasca-search-export-v0.1.md`)
- [x] Frontend integration (`frontend-stack-and-integration-v0.1.md`)
- [x] Technical design (`tasca-technical-design-v0.1.md`)
- [x] Terminology mapping (`terminology-mapping-v0.1.md`)
- [x] Security ADRs (`adr-001-*`, `adr-002-*`)

## Security gates

- [ ] Raw HTML in Markdown is disabled
- [ ] Mermaid `%%{init}%%` directives are stripped/forbidden
- [ ] Mermaid SVG is sanitized per ADR-002
- [ ] CSP is defined and enabled for the UI
- [ ] Admin actions require `TASCA_ADMIN_TOKEN`

## Consistency & reliability

- [ ] Per-table sequence allocation is atomic and monotonic
- [ ] `table.wait` timeout is a success response (empty sayings)
- [ ] `dedup_id` behavior is implemented (`return_existing`)
- [ ] `table.update` uses optimistic concurrency (`expected_version`)
- [ ] `table.control` writes audit control sayings and updates status atomically

## UX completeness (MVP)

- [ ] Watchtower: list + filter + join by code
- [ ] Mission Control: stream + board + seats
- [ ] Viewer/Admin modes correctly enable/disable controls and input

## Search / export

- [ ] FTS covers sayings + board + metadata
- [ ] JSONL export matches the documented shape
- [ ] Markdown export matches the documented template