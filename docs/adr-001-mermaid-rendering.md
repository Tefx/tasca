# ADR-001: Mermaid Rendering Strategy (v0.1)

## Status
Accepted

## Context

The whiteboard Web UI must render Markdown content that may include:

- GFM tables
- KaTeX math
- Mermaid diagrams (fenced code blocks)

Content may be attacker-controlled (LLM output and/or untrusted collaborators). Recent Mermaid security advisories show practical XSS risk when rendering and injecting Mermaid output unsafely.

Constraints:

- Local/LAN deployment
- Single-instance service (no multi-instance coordination in v0.1)
- Minimal ops burden

## Decision

### 1) Mermaid rendering

**Client-side rendering** in the Web UI (using official Mermaid APIs such as `parse`/`render`).

### 2) Security guardrails (mandatory)

- **Strip / forbid Mermaid init directives** (`%%{init: ...}%%`) by default.
  - If any overrides are supported in the future, they MUST be allowlisted.
- **Pin Mermaid to a known-safe version** and avoid unbounded `^` upgrades.
  - v0.1 recommendation: pin to **11.10.x exactly** (no caret upgrades). Minimum baseline: Mermaid >= 11.10.0.
- Treat Mermaid output as untrusted:
  - **Sanitize rendered SVG** with a strict allowlist.
  - Disallow `<script>`, `<foreignObject>`, and all `on*` attributes.
  - Restrict URL-bearing attributes (`href`, `xlink:href`, `url(...)`) to safe schemes and/or allowlisted destinations.
- Enforce a strong **CSP** for the UI.
- Disable raw HTML in Markdown by default.
- Sanitize Mermaid output SVG per ADR-002.

### 3) Sanitization strategy

Adopt **module-specific rendering + module-specific sanitization**:

- KaTeX: render with `trust: false`, then allowlist its expected output.
- Mermaid: SVG-specific sanitizer.

### 4) Export behavior

Exports (JSONL + Markdown) will **not** embed rendered Mermaid images/SVG in v0.1. Mermaid remains as code blocks.

## Rationale

- Minimal ops burden vs server-side rendering (which introduces headless browser dependencies and new attack surfaces).
- Faster iteration for v0.1 while maintaining a defensible security posture.
- Clear upgrade path: if public sharing/multi-tenant hosting is required later, reconsider server-side isolated rendering.

## Consequences

### Positive

- Simple deployment
- Interactive, low-latency rendering in the UI

### Negative

- Requires careful browser-side sanitization and CSP maintenance
- Complex diagrams may be heavy on client CPU (mitigate via limits and caching)

## References

- Mermaid security advisories:
  - https://github.com/mermaid-js/mermaid/security/advisories/GHSA-7rqq-prvp-x9jh
  - https://github.com/mermaid-js/mermaid/security/advisories/GHSA-8gwm-58g9-j8pw
