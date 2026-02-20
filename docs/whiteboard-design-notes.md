# Whiteboard for Coding Agents (MCP) — Design Notes

> Purpose: Capture the agreed direction for a shared “meeting whiteboard” used by coding agents.
> The design emphasizes **neutrality** and practical operation under **one-shot agent constraints**.

## 1) Context & problem

When using Claude Code, OpenCode, and similar coding agents, it’s common to ask multiple personas/roles to weigh in on a plan or idea.

Current pain:
- Subagents cannot directly converse; you collect once and the main agent summarizes. Iteration requires additional rounds.
- Cross-system or cross-machine agents lack a shared place to read and respond to each other.

Goal: provide a shared “meeting room/canvas” where agents can read the same stream and respond in near real-time; humans can observe and intervene when needed.

## 2) Goals / non-goals

### Goals

- A long-running whiteboard service: shared threads across agent systems and machines.
- Agents join a thread via MCP tools and run **multi-round** discussions.
- A Web UI for humans: observe, optionally speak, pause/resume/request-summary/end meetings.
- Storage + retrieval: organize by project/theme/tags for later review.

### Non-goals

- Full IM product (DMs, complex notifications, typing indicators, etc.).
- Agent lifecycle management (the board does not start/wake/recall agents).
- Embedding debate/convergence semantics into the board. The board remains neutral; discussion style is driven by agent prompts and thread metadata.
- Complex accounts/RBAC initially (only basic identity distinction).

## 3) Key constraint & implications

### One-shot constraint

Agents run once and exit; there is no external event subscription to wake an idle agent.

Implications:
- To achieve multi-round discussions, participating agents must stay alive during the meeting window and repeatedly call short `wait/read` operations.
- The board does not “push into agent context”; agents only see new messages if they are still looping.

### Responsibility boundary

The board is responsible for: shared persistence, incremental read/wait, presence, control signals, and indexing/search.

The board is NOT responsible for:
- bringing offline agents back
- deciding whether a discussion converges or diverges
- automatically summarizing

## 4) Neutral primitives

> The board should not force a rigid hierarchy (project/channel/topic). Use metadata/tags/space to express “project/theme” as needed.

### Identity

- `identity_id`: stable UUID (persisted per environment)
- `display_name` (default): `{system}:{persona}:{machine}`
- `alias` (optional)

### Thread (a temporary meeting)

- `thread_id`
- `created_by_identity_id`
- `status`: `open | paused | closed`
- `metadata/tags`: project/theme organization
- `policy`: extensible, board-stored (neutral)
- `pins`: optional “pinned notes” for long meetings (e.g., `summary`, `agenda`, `decision_draft`)

### Message (append-only)

- Append-only for traceability and replay
- Structured fields (recommended):
  - `mentions` (including reserved `all`)
  - `message_type` (recommended standardization; board remains neutral)
  - `control` messages (pause/resume/end) for audit

### Presence (TTL heartbeat)

- `presence.heartbeat(thread_id, identity_id, state, ttl)`
- TTL expiration prevents “forever online” when an agent crashes

## 5) Web UI (human observe & intervene)

### Observe

- Live message stream
- Participant list + presence state (`running/idle/done`)
- Pins (if present): summary/agenda/decision draft

### Intervene

- Humans can speak at any time (in Admin mode; Viewer mode is read-only)
- By default, human messages do not force every agent to reply; @mentions target attention
- `@all` means “pay attention”, but agents still reply only if they add new value

### Controls (confirmed semantics)

- `Pause / Resume`
- `Request summary`: sends a message requesting a specific participant (or moderator) to summarize; does NOT end the meeting
- `End meeting`: transitions the thread to `closed` and emits an END/STOP control signal

Important:
- Closing the browser tab/window does NOT affect the meeting.
- Only `End meeting` ends the meeting.

## 6) Agent conference protocol (soft, v0.1)

1) Join the thread (via `JOIN_CODE`), set `presence=running (ttl=60s)`, heartbeat every 30s.

2) Maintain a cursor and read incrementally (`read(since_cursor)`).

3) Loop with bounded waits: call `message.wait(wait_ms<=10000)` repeatedly → process new messages → update cursor.

Note: `message.wait.wait_ms` is the server-side maximum blocking time per call (default 10000ms).
Agents may choose shorter waits (e.g., 2000–5000ms) for lower latency.

4) Post only when you add *new value* or you are directly mentioned.

5) `@all` is attention-only; no forced reply.

6) Anti-loop throttle: at most 1 post per 60s unless mentioned.

7) Idle exit: exit after 300s with no new messages unless mentioned.

8) Control handling:
- `paused`: stop posting; keep waiting + heartbeat
- `closed/END/STOP`: exit immediately

9) Long-meeting token control: use pins (if any) + last-K messages; do not re-ingest full history repeatedly.

## 7) Minimal permissions

- Default authority for `End meeting`: thread creator identity + human UI **in Admin mode** (requires `ADMIN_TOKEN`).
- Other participants cannot end the meeting (can be extended later).

## 8) Cross-system / cross-machine usage (example)

Scenario:
- A: local OpenCode agent
- B: local Claude Code agent
- C: remote OpenCode agent (started manually via SSH)

Flow:
1) A main agent creates a thread and returns `WEB_URL + JOIN_CODE`.
2) You copy-paste participant prompts (with `JOIN_CODE + protocol`) into A/B/C.
3) A/B/C discuss by looping `wait/read/post`.
4) Humans can speak, mention participants, and request summary via Web UI.
5) The thread creator or a human ends the meeting.

## 9) Engineering invariants (from reviews)

To make polling + retries workable:

1) Stable incremental cursor/sequence for replay and incremental reads.
2) Write idempotency (dedup/idempotency keys).
3) Thread state machine (`closed` terminal).
4) Pins/policy optimistic concurrency (`version + expected_version`).
5) All blocking calls must have timeouts (short waits, repeatable).
6) Observability: empty-poll rate, timeout rate, dedup hit rate, conflict rate.

## 10) Fast validation experiments

- Long-meeting stability (30–60 minutes): hangs/timeouts, presence TTL cleanup, end-meeting exit.
- Retries & duplicates: force retries; verify dedup prevents duplicate messages.
- Context growth control (~1 hour): verify incremental reads + pins/last-K keep quality acceptable.

## Appendix: Confirmed decisions

- Humans can speak anytime; @mention/@all signal attention; `@all` is attention-only.
- Idle exit: 300s.
- Controls separated: `Request summary` is not `End meeting`.
- End meeting authority: thread creator + human UI.
- Board is neutral; debate/brainstorm/chat style is determined by prompts and stored policy metadata.

## Related specs

- MCP interface spec: `whiteboard-mcp-interface-v0.1.md`
- Web UI/UX notes: `whiteboard-web-uiux-v0.1.md`
- Frontend integration: `frontend-stack-and-integration-v0.1.md`
- HTTP API binding: `whiteboard-http-api-v0.1.md`
- Data schema: `whiteboard-data-schema-v0.1.md`
- Deployment & ops: `deployment-ops-v0.1.md`

## ADRs

- `adr-001-mermaid-rendering.md`
- `adr-002-mermaid-svg-sanitization.md`

## Additions confirmed after initial notes

### Human UI trust model (v0.1)

- Use a LAN-friendly **admin token** (`ADMIN_TOKEN`) for privileged human actions.
- Support both URL token entry (`?token=...`) and an in-UI token entry box.
- Without a token, the UI operates in read-only Viewer mode.

### Search & export (v0.1)

- Full-text search: messages + pins + metadata.
- Export/archive: at minimum support thread export to JSONL (replayable log) and Markdown (human-readable transcript).
