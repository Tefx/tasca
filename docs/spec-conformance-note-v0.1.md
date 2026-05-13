# Spec Conformance Note (v0.1)

**Date:** 2026-02-22
**Commit:** fdc0271
**Status:** READY FOR RELEASE

## Overview

This document summarizes conformance to the Tasca specification documents.

## MCP Interface Conformance

| Spec Section | Status | Notes |
|--------------|--------|-------|
| patron_register | ✓ IMPLEMENTED WITH COMPAT FIELDS | `display_name`, legacy `name`, `kind`, `alias`, `meta`, `patron_id`, and `dedup_id` supported via shared patron registration operation |
| patron_get | ✓ IMPLEMENTED | Returns canonical patron object plus compatibility fields |
| table_create | ✓ IMPLEMENTED WITH COMPAT FIELDS | `title`/`question`, `created_by`/`creator_patron_id`, `host_ids`, `metadata`, `policy`, `board`, and `dedup_id` supported via shared table creation operation |
| table_get | ✓ IMPLEMENTED | Returns current table shape |
| table_join | ✓ IMPLEMENTED | `table_id` or `invite_code`, `history_limit`, `history_max_bytes` supported |
| table_list | ✓ IMPLEMENTED EXTENSION | Runtime default `status="open"` |
| table_delete_batch | ✓ IMPLEMENTED EXTENSION | Closed-only, max 100 IDs, all-or-nothing; returns `deleted_count`, `failed`, and `deleted_ids` |
| table_export | ✓ IMPLEMENTED EXTENSION | Shared export operation; MCP response contains `content`, `format`, and `table_id` |
| table_say | ✓ IMPLEMENTED WITH COMPAT NOTES | Shared append/state/limits operation; MCP owns mentions, idempotency, envelope/guidance. `saying_type` and `reply_to_sequence` are accepted for compatibility/telemetry and do not change persisted fields in v0.1 |
| table_listen | ✓ IMPLEMENTED | Runtime defaults `since_sequence=-1`, `limit=50` |
| table_control | ✓ IMPLEMENTED | Shared atomic table-control operation appends CONTROL saying and updates status in one storage transaction |
| table_update | ✓ IMPLEMENTED | Runtime signature is `table_id`, `expected_version`, `patch`, `speaker_name`, optional `patron_id`, optional `dedup_id` |
| table_wait | ✓ IMPLEMENTED | Runtime defaults `since_sequence=-1`, `wait_ms=10000`, `limit=50`, `include_table=false`; empty timeout is success |
| seat_heartbeat | ✓ IMPLEMENTED | `state` default `running`, `ttl_ms` default `60000`, legacy `seat_id` accepted |
| seat_list | ✓ IMPLEMENTED | `active_only` default `true` |
| connect / connection_status | ✓ IMPLEMENTED EXTENSION | MCP proxy/local-mode control tools |

## HTTP API Conformance

| Endpoint | Status | Notes |
|----------|--------|-------|
| POST /api/v1/patrons | ✓ IMPLEMENTED | Shared patron registration operation; canonical and compatibility fields returned |
| GET /api/v1/patrons/{id} | ✓ IMPLEMENTED | Patron lookup with canonical and compatibility fields |
| POST /api/v1/tables | ✓ IMPLEMENTED | Admin token required; shared table creation operation; canonical and compatibility fields returned |
| GET /api/v1/tables | ✓ IMPLEMENTED | List tables |
| GET /api/v1/tables/{id} | ✓ IMPLEMENTED | Get table by ID |
| PUT /api/v1/tables/{id}?expected_version=N | ✓ IMPLEMENTED | Admin token required; optimistic concurrency update |
| DELETE /api/v1/tables/{id} | ✓ IMPLEMENTED | Admin token required |
| POST /api/v1/tables/actions/batch-delete | ✓ IMPLEMENTED | Admin token required; shared closed-only all-or-nothing batch delete |
| POST /api/v1/tables/{id}/control | ✓ IMPLEMENTED | Admin token required; shared atomic table-control operation; accepts optional `dedup_id` for compatibility |
| POST /api/v1/tables/{id}/sayings | ✓ IMPLEMENTED | Admin token required; shared append/state/limits behavior; REST `Saying` response shape |
| GET /api/v1/tables/{id}/sayings | ✓ IMPLEMENTED | `since_sequence` and `limit`; returns `sayings` and `next_sequence` |
| GET /api/v1/tables/{id}/sayings/wait | ✓ IMPLEMENTED | REST long-poll uses `timeout` seconds, not MCP `wait_ms`; returns `timeout` flag |
| GET /api/v1/tables/{id}/export/{jsonl,markdown} | ✓ IMPLEMENTED | Shared export operation; HTTP owns download/media-type shaping |

## Security Conformance

| Requirement | Status | Notes |
|-------------|--------|-------|
| ADR-001: Mermaid Rendering | ✓ CONFORMS | Init directives stripped, trust handler configured |
| ADR-002: SVG Sanitization | ✓ CONFORMS | Allowlist enforced, dangerous elements removed |
| CSP Headers | ✓ CONFORMS | Production mode CSP verified |
| Admin Token | ✓ CONFORMS | Required for admin endpoints |

## Deviations from Spec

| Item | Deviation | Rationale |
|------|-----------|-----------|
| invite_code | Currently mirrors `table_id` | Backward compatibility and local/LAN simplicity |
| MCP runtime tool names | FastMCP exposes unprefixed names such as `table_create`; docs keep conceptual `tasca.*` names via `tool_contracts.py` `spec_name` | Tool discovery/runtime convention differs from conceptual namespace |
| REST wait query | HTTP wait uses `timeout` seconds rather than MCP `wait_ms` milliseconds and omits MCP `include_table`/`limit` fields | REST route predates MCP wait schema and preserves browser/UI contract |
| REST/MCP response envelopes | Shared operations are common, but HTTP and MCP responses are not envelope-identical | Transport compatibility; routes/entrypoints shape public protocol responses locally |
| table_say optional fields | MCP accepts `saying_type` and `reply_to_sequence`, but v0.1 persistence does not yet store them as distinct fields | Compatibility input retained without inventing new storage behavior |

## Test Coverage

Historical release gate counts remain recorded in prior evidence. For the current shared-operation doc sync, implementation-backed checks used these anchors:

- MCP contract/default tests: `tests/unit/mcp/test_tool_contracts.py`
- MCP tool discovery and spec-field tests: `tests/integration/test_mcp.py`
- REST table/update/control/batch-delete route tests: `tests/unit/api/test_tables_routes.py`
- REST sayings wait/append route tests: `tests/unit/api/test_sayings_routes.py`
- Shared operation unit tests: `tests/unit/services/operations/test_table_control.py`, `tests/unit/services/test_registration_creation_operations.py`

## Known Limitations

1. **UX Completeness (MVP-scoped)**: Watchtower, Mission Control, and Viewer/Admin modes not implemented
2. **API Integration Tests**: Require running server - not part of CI
3. **Real Claude Code Session**: STDIO simulation used for field validation

## Sign-off

- Gate freeze: ✓
- Checklist complete: ✓
- Independent review: ✓ CONDITIONAL PASS
- Spec conformance: ✓ (this document)
