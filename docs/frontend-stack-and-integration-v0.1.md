# Frontend Stack & FastAPI Integration (v0.1)

## Decision

Use a **React + TypeScript + Vite SPA** for the Web UI, integrated with a **Python (FastAPI) backend**.

This is the recommended “Option A” frontend choice, realized via the practical “Scheme B” integration model:

- Dev: Vite dev server (HMR) + FastAPI API server
- Prod: Vite build artifacts served by FastAPI (single origin) or by a reverse proxy

## Development setup (recommended)

### Processes

- Frontend: `vite` dev server
- Backend: `uvicorn` / FastAPI

### API routing

- Use a Vite dev proxy for `/api/*` to the FastAPI server.

HTTP interface reference:

- See `whiteboard-http-api-v0.1.md` for the `/api/v1/...` endpoint bindings.

Rationale:

- Best DX (HMR)
- Keeps backend and frontend concerns separated
- Minimizes prompt friction for agentic coding

## Production setup (recommended)

### Single-origin (simplest)

- `vite build` produces static assets.
- FastAPI serves:
  - `/api/*` as JSON
  - `/` and static assets for the SPA

Benefits:

- Easier CSP and security header control
- Simpler local/LAN deployment

### Reverse proxy (optional)

- Serve static assets via Nginx/Caddy
- Proxy `/api/*` to FastAPI

## Security implications

- Prefer single-origin so CSP can be enforced consistently.
- Mermaid rendering follows ADR-001 and ADR-002.
- Raw HTML in Markdown disabled by default.

## References

- FastAPI project generation / full-stack template (React + TS + Vite):
  - https://fastapi.tiangolo.com/project-generation/
