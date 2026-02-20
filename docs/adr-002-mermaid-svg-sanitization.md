# ADR-002: Mermaid SVG Sanitization Policy (v0.1)

## Status
Accepted

## Context

ADR-001 selects **client-side Mermaid rendering**. Mermaid output is SVG/HTML-like markup and MUST be treated as untrusted.

Goal: restrict Mermaid-rendered SVG to a safe “graphics + text” subset that prevents XSS, navigation abuse, and unexpected resource loading.

Assumptions already accepted:

- Raw HTML in Markdown is disabled by default.
- Mermaid init directives (`%%{init: ...}%%`) are stripped/forbidden by default.
- Strong CSP is planned.

## Decision

### 1) Allowlist-first SVG policy (strict)

Sanitization is **strict allowlist**. Anything not explicitly allowed is removed.

#### Allowed SVG tags (v0.1)

- Containers/structure: `svg`, `g`, `defs`
- Basic shapes: `path`, `rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`
- Text: `text`, `tspan`
- Markers: `marker`
- Gradients (optional but commonly needed): `linearGradient`, `radialGradient`, `stop`

#### Allowed attributes (v0.1)

Geometry/viewport:

- `viewBox`, `width`, `height`
- `x`, `y`, `cx`, `cy`, `r`, `rx`, `ry`
- `x1`, `y1`, `x2`, `y2`
- `d`, `points`

Presentation attributes (prefer these over CSS):

- `fill`, `fill-opacity`
- `stroke`, `stroke-opacity`, `stroke-width`
- `stroke-linecap`, `stroke-linejoin`, `stroke-dasharray`
- `opacity`

Text attributes:

- `font-family`, `font-size`, `font-weight`, `text-anchor`, `dominant-baseline`

Identification / accessibility:

- `id`, `class`, `role`, `aria-label`

Internal-reference attributes (subject to URL rules below):

- `marker-start`, `marker-mid`, `marker-end`
- `fill`/`stroke` when they contain `url(#...)`

### 2) Hard forbids (non-negotiable)

#### Forbidden tags

- `script`
- `foreignObject`
- `iframe`, `object`, `embed`
- `audio`, `video`
- `image`
- `a`
- SMIL/animation tags: `animate`, `set`, `animateTransform`, `animateMotion`

> Note: `use` is **forbidden by default** in v0.1. It can be enabled later only if Mermaid output requires it and only with strict internal `#id` reference enforcement.

#### Forbidden attributes

- Any `on*` event handler attributes
- `style` (inline styles)
- Namespace-based external linking attributes by default (e.g. `xlink:*`) unless explicitly needed and constrained

### 3) URL / reference handling rules (strict)

The default policy is: **internal fragment references only**.

- For any URL-bearing attribute (including `href`, `xlink:href`) if enabled later:
  - allow only `^#[A-Za-z_][\w:.-]*$`
- For `url(...)` usages inside attributes:
  - allow only `url(#id)` (quotes allowed, but must resolve to an internal `#id`)
- Reject all of:
  - `javascript:`
  - `data:`
  - `file:`
  - `http(s):`
  - relative URLs

### 4) Style handling

- `<style>`: forbidden
- `style="..."`: forbidden

Rationale: CSS increases attack surface (`url()`, `@import`, browser quirks). v0.1 prioritizes safety over perfect styling fidelity.

### 5) Implementation guidance

- Perform sanitization **in the browser** immediately after Mermaid renders and before inserting into the DOM.
- Use a mature sanitizer with explicit allowlists (e.g., **DOMPurify** configured for SVG) plus hooks to validate URL-like values.
- Do **not** inject raw strings via `innerHTML`.
  - Prefer `DOMParser` to parse sanitized SVG into a document, then import/append the resulting nodes.

### 6) Testing requirement

Maintain a small regression corpus:

- “Known-bad” SVG/XSS payload samples
- A set of real Mermaid outputs from your expected diagrams

Fail the build if any forbidden tag/attribute survives sanitization.

## Consequences

- Some Mermaid outputs may lose styling or fail to render if Mermaid relies on forbidden features.
  - Mitigation: iterate allowlist with test coverage, or switch to server-side rasterization (PNG) in higher-risk deployments.
