# dup_guardrails.gate Review Evidence

## refs Read Confirmation

- `evidence/dup_guardrails-parity-contract-capture.md` — Read lines 25-65 and 67-84. Key insight: the preservation register explicitly separates shared semantics from transport-local shaping and names blocker-class drift for MCP control atomicity, limits ownership, export orchestration, and MCP schema ownership; it also classifies compatibility bindings versus cleanup candidates.
- `tests/unit/test_dup_guardrails_control_red.py` — Read lines 28-39 and 96-200. Key insight: red tests are real executable tests, not static fixtures; they assert MCP rollback on control update failure, documented HTTP control payload acceptance, canonical REST/MCP control audit content, and REST limit error/body preservation.
- `docs/tasca-mcp-interface-v0.1.md` — Read lines 17-24, 26-36, 165-178, and 352-370. Key insight: MCP requires idempotent writes, server-side abuse limits, closed as terminal, and table.control to append a CONTROL saying and update status with atomicity expected.
- `docs/tasca-http-api-v0.1.md` — Read lines 16-28 and 46-65. Key insight: HTTP admin-required operations must validate bearer auth; most HTTP endpoints are thin wrappers over MCP semantics; HTTP control body is documented as `action`, optional `reason`, and `dedup_id`.
- `docs/tasca-technical-design-v0.1.md` — Read lines 102-120 and 122-199. Key insight: `table.say`, `table.control`, and `table.update` are required transaction boundaries, while public consolidation mandates consistent error envelopes, HTTP admin auth, canonical dedup scope, and human speaker semantics.
- `src/tasca/shell/api/routes/tables_control.py` — Read lines 50-63, 70-79, 106-172, and 188-191. Key insight: REST control currently requires `speaker_name`, lacks `dedup_id` in the request model, builds REST audit text without `Reason:`, and delegates append+status update to `atomic_control_table`.
- `src/tasca/shell/mcp/entrypoints.py` — Read lines 879-981 and 1054-1188. Key insight: MCP sayings use `append_saying_with_limits`; MCP control currently appends the control saying and later calls `update_table`, leaving a real split-write atomicity gap under update failure.
- `src/tasca/shell/mcp/server.py` — Read lines 338-571. Key insight: FastMCP tool signatures and annotations are the public schema surface; `table_control` exposes `speaker_name`, optional `patron_id`, `reason`, and `dedup_id`, while `table_say` exposes limit-relevant message and compatibility fields.

## Gate Review Report

| Step ID | Status | Evidence Quality | Concerns |
|---|---|---|---|
| `dup_guardrails.parity-contract-capture` | Complete artifact present | Strong: static register cites specs, implementation anchors, tests, shared semantics, transport-local shaping, compatibility bindings, cleanup candidates, and blocker-class drift. | Warning: register does not prove downstream plan ownership directly because this auditor was barred from vectl inspection; ownership is inferred from the named `dup_foundation`/downstream remediation scope in this gate request. |
| `dup_guardrails.control-atomicity-red-tests` | Complete executable guardrail present | Strong: targeted pytest file fails in the expected places and passes the preservation check for REST limit error semantics. Command: `.venv/bin/python -m pytest tests/unit/test_dup_guardrails_control_red.py`; exit code `1`; result `3 failed, 1 passed`. | Note: these are expected-red guardrails and must not be treated as current-regression failures for this gate; they are acceptance-boundary tests for downstream remediation. |

## Wiring Audit Results

| Check | Result | Evidence |
|---|---|---|
| W1 Shared semantics identified before extraction | PASS | Register rows R4-R9 and Preservation Register lines 51-58 classify control, limits, export, and schema semantics separately from response envelopes. |
| W2 Transport-local shaping preserved | PASS | Register lines 53-58 list REST vs MCP response/body/auth/envelope distinctions; `tables_control.py:50-56` and `server.py:512-530` confirm current surfaces differ. |
| W3 Atomic control blocker made executable | PASS | Red test `test_mcp_control_rolls_back_control_saying_when_status_update_fails` at `tests/unit/test_dup_guardrails_control_red.py:96-123`; run fails because a control saying remains after forced status update failure. |
| W4 HTTP documented control shape gap made executable | PASS | Red test at `tests/unit/test_dup_guardrails_control_red.py:126-139`; run fails with `422` because REST currently requires `speaker_name`, despite HTTP spec body at `docs/tasca-http-api-v0.1.md:63-65`. |
| W5 Control audit content convergence gap made executable | PASS | Red test at `tests/unit/test_dup_guardrails_control_red.py:142-175`; run fails because REST emits `Completed` while MCP/canonical expectation uses `Reason: Completed`. |
| W6 Limit adoption/backward compatibility guarded | PASS | Red/guardrail test at `tests/unit/test_dup_guardrails_control_red.py:178-200` passes, proving REST limit failure shape remains a protected transport-local behavior while limit ownership moves. |
| W7 MCP schema ownership constrained | PASS | Register lines 64-65 and `server.py:338-571` identify schema surface as public and non-incidental; needed field-level golden test is correctly recorded as downstream proof gap, not silently omitted. |
| W8 Existing REST/MCP behavior not destabilized by guardrail artifacts | PASS | Command `.venv/bin/python -m pytest tests/unit/api/test_tables_routes.py tests/unit/mcp/test_mcp_server.py -q`; exit code `0`; result `152 passed`. |

## Escape Hatch Audit Results

- Reviewed files intersect many `@invar:allow` annotations: `tables_control.py:70-72`, `entrypoints.py:1,141,153,166,181,272,311,397,432,522,553,609,659,716,743,797,849,880,985,1054,1071,1089,1192,1280,1374,1387,1401,1407`, and `server.py:1,338,357,370,387,407,419,431,444,458,495,512,533,554,574,594,607,623,809`.
- Result: WARNING, not blocker for this gate. The reviewed escape hatches are pre-existing shell/protocol allowances and the register/test artifacts do not introduce new product-code hatches. However, high density around MCP entrypoints and server schemas raises anomaly score and justifies downstream deep review of extraction diffs.

## Decision Basis

- Shared semantics frozen: Yes. The preservation register constrains control atomicity/status transition, closed-state restrictions, idempotency, saying limits, export ordering/core formatting, and MCP schema stability with source anchors. Red tests turn the highest-risk control and limit commitments into executable acceptance boundaries.
- Transport-local shaping frozen: Yes. REST admin auth, REST error/body shapes, REST export/download shaping, MCP success/error envelopes, MCP compatibility fields, and MCP schema annotations are called out as local/binding behavior rather than extraction targets.
- Remaining blocker-class ambiguity: No unowned blocker remains for the guardrail phase. Known product blockers remain intentionally open for downstream `dup_foundation`/duplicate-abstraction remediation: MCP control atomicity, REST documented control payload adoption, control audit formatting convergence, schema golden coverage, and cross-transport limit ownership. Because these are explicitly named as downstream obligations, they do not block opening the next phase.

## Real vs Fixture Distinction

- Red tests are real pytest tests exercising in-memory SQLite/FastAPI/MCP entrypoint code; they are expected to fail until remediation lands.
- Preservation register is static audit evidence, not runtime proof. Its claims were spot-checked against required source files and specs in this gate.

## Verification Run

- `.venv/bin/python -m pytest tests/unit/test_dup_guardrails_control_red.py` → exit code `1`; expected-red result `3 failed, 1 passed`.
- `.venv/bin/python -m pytest tests/unit/api/test_tables_routes.py tests/unit/mcp/test_mcp_server.py -q` → exit code `0`; result `152 passed`.
- `grep`-style escape hatch scan via MCP grep tools over reviewed source files found the listed `@invar:allow` annotations.

## Gate Decision

Gate Decision: OPEN
verdict: PASS
blockers: []
gate_open_allowed: true
orchestrator_action_hint: COMPLETE
