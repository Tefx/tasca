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

- [x] Raw HTML in Markdown is disabled
  - Evidence: `security.katex_trust_false` - markdown.tsx disables HTML, KaTeX trust=false
- [x] Mermaid `%%{init}%%` directives are stripped/forbidden
  - Evidence: `security.mermaid_init_stripping` - mermaid.tsx strips init directives
- [x] Mermaid SVG is sanitized per ADR-002
  - Evidence: `security_fix.b1-mermaid-render-wire` - mermaid.render() output passes through sanitizeSvg()
  - Evidence: `security_fix.b2-svg-allowlist-fix` - SVG allowlist aligned with ADR-002
- [x] CSP is defined and enabled for the UI
  - Evidence: `integration_l2.security_headers_assert` - CSP headers verified in production mode
  - Evidence: `security_fix.b3-csp-base-uri` - base-uri set to 'none'
  - Evidence: `security_fix.s3-csp-middleware-test` - 7 CSP middleware tests added
- [x] Admin actions require `TASCA_ADMIN_TOKEN`
  - Evidence: `integration_l2.harness_run` - POST /api/v1/tables → 401 without token

## Consistency & reliability

- [x] Per-table sequence allocation is atomic and monotonic
  - Evidence: `integration_l2.harness_run` - sequences monotonic in say/listen tests
- [x] `table.wait` timeout is a success response (empty sayings)
  - Evidence: `integration_l2.observability_assertions` - wait_timeout event logged
  - Evidence: `l3_fix.b1-missing-tools` - table_wait tool implemented
- [x] `dedup_id` behavior is implemented (`return_existing`)
  - Evidence: `integration_l2.observability_assertions` - dedup_hit event logged
- [x] `table.update` uses optimistic concurrency (`expected_version`)
  - Evidence: `l3_fix.b1-missing-tools` - table_update tool implemented with version check
  - Evidence: `tests/unit/test_table_version.py` - version conflict tests
- [x] `table.control` writes audit control sayings and updates status atomically
  - Evidence: `l3_fix.b1-missing-tools` - table_control tool implemented
  - Evidence: `integration_fix.s4-missing-mcp-tools-tracking` - TODO stubs documented

## UX completeness (MVP)

- [ ] Watchtower: list + filter + join by code
  - DEFERRED: Not implemented in MVP scope
- [ ] Mission Control: stream + board + seats
  - DEFERRED: Not implemented in MVP scope
- [ ] Viewer/Admin modes correctly enable/disable controls and input
  - DEFERRED: Not implemented in MVP scope

## Search / export

- [x] FTS covers sayings + board + metadata
  - Evidence: `search_export` phase - SQLite FTS5 implementation
- [x] JSONL export matches the documented shape
  - Evidence: `search_export.jsonl-export` - JSONL export implemented
- [x] Markdown export matches the documented template
  - Evidence: `search_export.md-export` - Markdown export implemented

## MCP Tools (v0.1)

- [x] patron_register - Evidence: `integration_l2.harness_run`
- [x] patron_get - Evidence: `integration_l2.harness_run`
- [x] table_create - Evidence: `integration_l2.harness_run`
- [x] table_get - Evidence: `integration_l2.harness_run`
- [x] table_join - Evidence: `l3_fix.b4-table-join-invite-code`
- [x] table_say - Evidence: `l3_fix.b2-table-say-signature`
- [x] table_listen - Evidence: `l3_fix.s1-table-listen-next-sequence`
- [x] table_control - Evidence: `l3_fix.b1-missing-tools`
- [x] table_update - Evidence: `l3_fix.b1-missing-tools`
- [x] table_wait - Evidence: `l3_fix.b1-missing-tools`
- [x] seat_heartbeat - Evidence: `l3_fix.b3-seat-heartbeat-signature`
- [x] seat_list - Evidence: `integration_l2.harness_run`

## Observability (§9)

- [x] table.create/update/control logging
  - Evidence: `integration_l2.observability-fix` - structured logging added
- [x] table.say logging (table_id, sequence, speaker_kind)
  - Evidence: `integration_l2.observability-fix`
- [x] dedup hits logging
  - Evidence: `integration_l2.observability-fix`
- [x] wait timeouts/returns logging
  - Evidence: `integration_l2.observability-fix`

## Gate Sign-off

- [x] Freeze snapshot captured: `gate.freeze` - commit fdc0271
- [ ] Independent review sign-off: PENDING
- [ ] Spec conformance note: PENDING