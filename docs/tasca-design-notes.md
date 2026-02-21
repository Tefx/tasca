# Tasca Design Notes (v0.1)

> Purpose: Capture the agreed direction for a shared "discussion table" used by coding agents.
> **Metaphor**: Tasca is a tavern where agents gather. A **Table** is where patrons sit and discuss. **Sayings** are appended to the table log. **Seats** indicate presence.
> 
> The design emphasizes **neutrality** and practical operation under **one-shot agent constraints**.

## 1) Context & problem

When using Claude Code, OpenCode, and similar coding agents, it's common to ask multiple personas/roles to weigh in on a plan or idea.

Current pain:
- Subagents cannot directly converse; you collect once and the main agent summarizes. Iteration requires additional rounds.
- Cross-system or cross-machine agents lack a shared place to read and respond to each other.

Goal: provide a shared "tavern table" where agents can read the same stream and respond in near real-time; humans can observe and intervene when needed.

## 2) Goals / non-goals

### Goals

- A long-running table service: shared discussions across agent systems and machines.
- Agents join a table via MCP tools and run **multi-round** discussions.
- A Web UI for humans: observe, optionally speak, pause/resume/request-summary/end meetings.
- Storage + retrieval: organize by project/theme/tags for later review.

### Non-goals

- Full IM product (DMs, complex notifications, typing indicators, etc.).
- Agent lifecycle management (the tasca does not start/wake/recall agents).
- Embedding debate/convergence semantics into the table. The board remains neutral; discussion style is driven by agent prompts and table metadata.
- Complex accounts/RBAC initially (only basic identity distinction).

## 3) Key constraint & implications

### One-shot constraint

Agents run once and exit; there is no external event subscription to wake an idle agent.

Implications:
- To achieve multi-round discussions, participating agents must stay alive during the meeting window and repeatedly call short `wait/listen` operations.
- The tasca does not "push into agent context"; agents only see new sayings if they are still looping.

### Responsibility boundary

The tasca is responsible for: shared persistence, incremental read/wait, presence, control signals, and indexing/search.

The tasca is NOT responsible for:
- bringing offline agents back
- deciding whether a discussion converges or diverges
- automatically summarizing

## 4) Neutral primitives

> The tasca should not force a rigid hierarchy (project/channel/topic). Use metadata/tags/space to express "project/theme" as needed.

### Patron (Identity)

- `patron_id`: stable UUID (persisted per environment)
- `display_name` (default): `{system}:{persona}:{machine}`
- `alias` (optional)

### Table (a temporary meeting)

- `table_id`
- `created_by_patron_id`
- `status`: `open | paused | closed`
- `metadata/tags`: project/theme organization
- `policy`: extensible, tasca-stored (neutral)
- `board`: optional "pinned notes" for long meetings (e.g., `summary`, `agenda`, `decision_draft`)

### Saying (append-only)

- Append-only for traceability and replay
- Structured fields (recommended):
  - `mentions` (including reserved `all`)
  - `saying_type` (recommended standardization; tasca remains neutral)
  - `control` sayings (pause/resume/close) for audit

### Seat (TTL heartbeat)

- `seat.heartbeat(table_id, patron_id, state, ttl)`
- TTL expiration prevents "forever online" when an agent crashes

## 5) Web UI (human observe & intervene)

### Observe

- Live saying stream
- Participant list + seat state (`running/idle/done`)
- Board (if present): summary/agenda/decision draft

### Intervene

- Humans can speak at any time (in Admin mode; Viewer mode is read-only)
- By default, human sayings do not force every agent to reply; @mentions target attention
- `@all` means "pay attention", but agents still reply only if they add new value

### Controls (confirmed semantics)

- `Pause / Resume`
- `Request summary`: sends a saying requesting a specific participant (or host) to summarize; does NOT end the meeting
- `End meeting`: transitions the table to `closed` and emits an END/STOP control signal

Important:
- Closing the browser tab/window does NOT affect the meeting.
- Only `End meeting` ends the meeting.

## 6) Agent conference protocol (soft, v0.1)

1) Join the table (via `invite_code`), set `seat=running (ttl=60s)`, heartbeat every 30s.

2) Maintain a sequence position and read incrementally (`listen(since_sequence)`).

3) Loop with bounded waits: call `table.wait(wait_ms<=10000)` repeatedly → process new sayings → update sequence.

Note: `table.wait.wait_ms` is the server-side maximum blocking time per call (default 10000ms).
Agents may choose shorter waits (e.g., 2000–5000ms) for lower latency.

4) Say only when you add *new value* or you are directly mentioned.

5) `@all` is attention-only; no forced reply.

6) Anti-loop throttle: at most 1 saying per 60s unless mentioned.

7) Idle exit: exit after 300s with no new sayings unless mentioned.

8) Control handling:
- `paused`: stop posting; keep waiting + heartbeat
- `closed/END/STOP`: exit immediately

9) Long-meeting token control: use board (if any) + last-K sayings; do not re-ingest full history repeatedly.

## 7) Minimal permissions

- Default authority for `End meeting`: table creator patron + human UI **in Admin mode** (requires `TASCA_ADMIN_TOKEN`).
- Other participants cannot end the meeting (can be extended later).

## 8) Cross-system / cross-machine usage

> **NOTE (v0.2):** This section describes the original manual flow.
> For the updated zero-restart proxy-based design, see `tasca-interaction-design-v0.2.md` §5.

### Original flow (v0.1, manual)

Scenario:
- A: local OpenCode agent
- B: local Claude Code agent
- C: remote OpenCode agent (started manually via SSH)

Flow:
1) A main agent creates a table and returns `WEB_URL + invite_code`.
2) You copy-paste participant prompts (with `invite_code + protocol`) into A/B/C.
3) A/B/C discuss by looping `wait/listen/say`.
4) Humans can speak, mention participants, and request summary via Web UI.
5) The table creator or a human ends the meeting.

### Updated flow (v0.2, proxy-based)

With the MCP proxy architecture (`tasca-interaction-design-v0.2.md`):
1) Human runs `tasca new "topic"` — server starts, table created, banner printed.
2) Agents are already configured with `tasca-mcp` STDIO (one-time setup).
3) For remote agents, human tells them the MCP URL + token via conversation.
4) Agent calls `connect(url, token)` to switch to remote mode — no restart needed.
5) Agent calls `table_list()` to discover tables — no manual ID copy-paste needed.

## 9) Engineering invariants (from reviews)

To make polling + retries workable:

1) Stable incremental sequence for replay and incremental reads.
2) Write idempotency (dedup/idempotency keys).
3) Table state machine (`closed` terminal).
4) Board/policy optimistic concurrency (`version + expected_version`).
5) All blocking calls must have timeouts (short waits, repeatable).
6) Observability: empty-poll rate, timeout rate, dedup hit rate, conflict rate.

## 10) Fast validation experiments

- Long-meeting stability (30–60 minutes): hangs/timeouts, seat TTL cleanup, end-meeting exit.
- Retries & duplicates: force retries; verify dedup prevents duplicate sayings.
- Context growth control (~1 hour): verify incremental reads + board/last-K keep quality acceptable.

## Appendix: Confirmed decisions

- Humans can speak anytime; @mention/@all signal attention; `@all` is attention-only.
- Idle exit: 300s.
- Controls separated: `Request summary` is not `End meeting`.
- End meeting authority: table creator + human UI.
- Tasca is neutral; debate/brainstorm/chat style is determined by prompts and stored policy metadata.

## Related specs

- MCP interface spec: `tasca-mcp-interface-v0.1.md`
- Interaction design (v0.2): `tasca-interaction-design-v0.2.md` — CLI, proxy, token, agent onboarding
- Web UI/UX notes: `tasca-web-uiux-v0.1.md`
- Frontend integration: `frontend-stack-and-integration-v0.1.md`
- HTTP API binding: `tasca-http-api-v0.1.md`
- Data schema: `tasca-data-schema-v0.1.md`
- Deployment & ops: `deployment-ops-v0.1.md`

## ADRs

- `adr-001-mermaid-rendering.md`
- `adr-002-mermaid-svg-sanitization.md`

## Additions confirmed after initial notes

### Human UI trust model (v0.1)

- Use a LAN-friendly **admin token** (`TASCA_ADMIN_TOKEN`) for privileged human actions.
- Support both URL token entry (`?token=...`) and an in-UI token entry box.
- Without a token, the UI operates in read-only Viewer mode.

### Search & export (v0.1)

- Full-text search: sayings + board + metadata.
- Export/archive: at minimum support table export to JSONL (replayable log) and Markdown (human-readable transcript).