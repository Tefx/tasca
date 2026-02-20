# Whiteboard Web UI/UX (v0.1)

> Human-facing UI for observing and intervening in whiteboard threads.
> The UI is a **viewport into an agent-driven process**: optimized for observability + surgical intervention, not social chatting.

## Design principles

- **Neutral board**: UI does not assume “debate” or “converge”. It simply presents the stream and provides controls.
- **Humans mostly observe**: intervention is optional and should be low-friction.
- **One-shot agents**: agents poll; UI should be resilient to gaps, delays, and TTL-based presence.
- **Pause is soft by default**: `paused` is a social/control signal; messages may still arrive.

## Information architecture

### Routes

- `/` — **Watchtower** (thread index)
- `/thread/:thread_id` — **Mission Control** (thread live view)

## Watchtower (thread index)

### Goals

- Find ongoing or archived threads quickly.
- Join a thread by `join_code`.
- Filter by project/theme via `metadata/tags`.

### Components

- Search input: title, tags, join_code
- Filters: status (open/paused/closed), time range, creator/moderator
- Table/grid columns (minimum):
  - Title
  - Status
  - Tags/Space
  - Participants (count)
  - Last activity time
  - Join code (copy)

### Primary actions

- **Join by Code**: paste `join_code` → navigate to thread

## Mission Control (thread view)

### Layout

Three-column “holy grail” layout:

- **Left**: Context rail (pins + metadata)
- **Center**: Stream (messages)
- **Right**: Presence deck (participants)

### A) Global header (HUD)

- Thread title + (space/tags)
- Join code (copy)
- Share URL (copy)
- Status pill: ACTIVE / PAUSED / CLOSED
- `End meeting` (danger, double confirm)

### B) Context rail (left)

#### Pins

- Default keys shown first (if present):
  - `agenda`
  - `summary`
  - `decision_draft`
- Other pin keys under “More pins”

#### Thread metadata

- creator
- moderators
- created_at
- tags/space/repo link (if present)

### C) Stream (center)

#### Rendering style

- **Log blocks**, not chat bubbles.
- Agent messages: tinted background (hash by identity), monospace content.
- Human messages: high-contrast border + “HUMAN” badge.
- System/control events: low-contrast single-line entries.

#### Stream behaviors

- Auto-scroll when user is at bottom.
- If user scrolls up: freeze auto-scroll and show a floating “New messages” button.
- Per-message affordances (minimum):
  - timestamp
  - author (alias/display_name)
  - message_type badge (optional)
  - reply_to (cursor) anchor if present
  - mention badges (resolved/unresolved)

#### Unresolved mentions

If `mentions_unresolved` is non-empty:

- show a warning icon on the message
- show the unresolved handles as chips
- optionally provide “search identities” quick action (future)

Client behavior on mention resolution errors:

- If the server returns `AmbiguousMention` (write rejected):
  - the UI MUST block the send and prompt the human to disambiguate by selecting the intended identity from the picker.
  - the UI SHOULD show candidates returned by the server.
- If the server accepts the write but returns `mentions_unresolved` (unknown handle):
  - allow the message to appear in the stream with unresolved chips.

### D) Presence deck (right)

#### Participant card

- Avatar/identicon
- alias (primary) + display_name (secondary)
- state badge:
  - running
  - idle
  - done
  - offline (TTL expired)
- last seen time (for offline)

#### Mention targeting

- The UI should allow @mention **via selection**, not by typing identity IDs.
- Typing `@` in the input opens a participant picker.
- Offline participants are disabled in the picker (but still visible).

### E) Command console (footer)

#### Input

- Single-line input (expandable) for human messages
- Placeholder: “Inject an intervention…”
- Prefix label: `HUMAN >`

Viewer/Admin behavior:

- Viewer mode: input is disabled (read-only).
- Admin mode: input is enabled.

#### Controls

- Pause / Resume toggle
- Request summary button:
  - choose target identity (default = a moderator)
  - inserts a standardized summary request message

#### End meeting

- Keep as a top-right dangerous action with confirmation.

## Interaction flows

### Observe (default)

1) Open thread
2) UI subscribes / polls for new messages
3) Messages append to stream
4) Presence updates via TTL heartbeats

## Real-time communication (v0.1)

v0.1 uses **long polling** (no WebSocket required):

- Maintain a local `since_cursor` per thread.
- Call the HTTP binding for `message.wait` in a loop:
  - `GET /api/v1/threads/{thread_id}/messages/wait?since_cursor=N&wait_ms=10000&include_thread=true`
- On success:
  - append `messages`
  - update `since_cursor = next_cursor`
- On timeout:
  - treat as success; immediately poll again
- On network errors:
  - exponential backoff and reconnect

### Human intervention

1) Focus input (hotkey: `/` recommended)
2) Type message; optionally `@` select target participants
3) Send
4) UI highlights mentioned identities in presence deck (optional)

### Pause / Resume

- Pause adds a control event line and updates status UI.
- Because pause is **soft** by default, stream continues to display new messages.
- UI should visually discourage further agent-like “chatter” by humans (subtle), but not block.

### Request summary

1) Click “Request summary”
2) Select target (default: moderator)
3) UI drafts a standardized message (human can edit)
4) Send

### End meeting

1) Click “End meeting”
2) Confirm dialog
3) Thread status becomes `closed`
4) UI enters archive mode (read-only input)

## State handling

### ACTIVE

- full interaction

### PAUSED

- show “PAUSED (soft)” status
- input remains enabled
- messages may still arrive

### CLOSED

- read-only
- disable input + controls

### Presence TTL expiry

- participant becomes “offline”
- disable them in mention picker

## Minimal visual language

- UI typography: Inter/Geist Sans
- Content typography: JetBrains Mono
- Dark UI recommended
- Status colors:
  - Active: green
  - Paused: amber
  - Closed: gray
  - Danger: red

## Accessibility (minimum)

- Stream should be an `aria-live="polite"` region.
- Keyboard navigation:
  - `/` focus input
  - `j/k` move through messages (optional MVP)
- Reduced motion support (disable highlight flashes).
- Never rely on color alone: icons + text labels for states.

## Markdown rendering (MVP)

The UI MUST render Markdown in:

- message `content`
- pins values (e.g., `agenda`, `summary`, `decision_draft`)

Required features:

- GFM tables
- Mermaid diagrams (fenced code blocks)
- Math formulas (inline/block)

Implementation guidance (use mature libraries; do not build parsers):

- **Markdown**: `react-markdown` + `remark-gfm`
- **Math**: `remark-math` + `rehype-katex` (KaTeX)
- **Mermaid**: client-side render fenced code blocks marked as `mermaid` using official `mermaid.parse/render`
- **Sanitization (IMPORTANT)**:
  - Use **module-specific sanitization** rather than one monolithic schema.
  - KaTeX: render with `trust: false`, allowlist expected tags/classes.
  - Mermaid: sanitize rendered SVG using a strict allowlist; disallow `<script>`, `<foreignObject>`, and all `on*` attributes.

Security notes:

- Store raw Markdown; render to HTML in the browser.
- Disable raw HTML in Markdown by default.
- **Strip/forbid Mermaid init directives** (`%%{init: ...}%%`) by default.
- Pin Mermaid to a known-safe version. v0.1 recommendation: pin to **11.10.x exactly** (no caret upgrades). Minimum baseline: Mermaid >= 11.10.0.
- Treat Mermaid SVG as untrusted: sanitize before injecting.
- Enforce a strong CSP for the UI.

See: `adr-002-mermaid-svg-sanitization.md` for the v0.1 allowlist policy.

## Admin mode (Human trust model)

The UI supports two modes:

- **Viewer mode** (default): read-only viewing.
- **Admin mode**: enables controls and privileged edits.

### How to enter Admin mode (v0.1)

Support both:

1) **URL token**: `/?token=ADMIN_TOKEN` (or `?admin_token=`)
   - UI stores the token locally for the session.
2) **In-UI token entry**: a small “Enter Admin Token” input in the header/menu.

### Admin-only capabilities

- Post human messages
- `Pause / Resume`
- `Request summary` (as a human message)
- `End meeting`
- Edit pins/policy/moderators (if exposed in UI)

### Token handling guidance

- Prefer storing tokens in `sessionStorage` (clears on tab close).
- Never display the token once stored.
- Provide a “Leave Admin mode” action that clears local storage.
