# Repository Structure (v0.1)

> This document defines the canonical repository structure for the tasca project.

## Overview

Tasca is a Python + React application with dual interfaces:
- **MCP Server** — for Claude Code, OpenCode, and other MCP-compatible agents
- **HTTP REST API** — for Web UI and HTTP tool users

```
tasca/
├── pyproject.toml              # Python project config (backend)
├── src/
│   └── tasca/
│       ├── __init__.py
│       ├── main.py             # FastAPI entrypoint
│       ├── core/               # Pure logic (@pre/@post + doctests, no I/O)
│       │   ├── __init__.py
│       │   ├── domain/         # Domain types
│       │   │   ├── __init__.py
│       │   │   ├── table.py    # Table, TableStatus, TableCreate
│       │   │   ├── saying.py   # Saying, Speaker, SpeakerKind
│       │   │   ├── seat.py     # Seat, SeatState
│       │   │   └── patron.py   # Patron, PatronCreate
│       │   ├── services/       # Business services
│       │   │   ├── __init__.py
│       │   │   ├── table_service.py
│       │   │   ├── saying_service.py
│       │   │   └── seat_service.py
│       │   └── contracts.py    # Protocols / type definitions
│       ├── shell/              # I/O layer (Result[T, E] return type)
│       │   ├── __init__.py
│       │   ├── api/            # FastAPI REST endpoints
│       │   │   ├── __init__.py
│       │   │   ├── routes/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── tables.py
│       │   │   │   ├── sayings.py
│       │   │   │   ├── seats.py
│       │   │   │   ├── patrons.py
│       │   │   │   ├── search.py
│       │   │   │   └── export.py
│       │   │   ├── deps.py     # Dependencies injection
│       │   │   └── auth.py     # Admin token validation
│       │   ├── mcp/            # MCP server (FastMCP)
│       │   │   ├── __init__.py
│       │   │   └── server.py   # MCP tools definition
│       │   └── storage/        # SQLite repositories
│       │       ├── __init__.py
│       │       ├── database.py # Connection + WAL config
│       │       ├── table_repo.py
│       │       ├── saying_repo.py
│       │       ├── seat_repo.py
│       │       ├── patron_repo.py
│       │       └── dedup_repo.py
│       └── config.py           # Settings (pydantic-settings)
├── web/                        # Frontend (React + TS + Vite)
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── public/
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── routes/
│       │   ├── Watchtower.tsx  # Table index
│       │   └── Table.tsx       # Mission Control
│       ├── components/
│       │   ├── Stream.tsx      # Saying list
│       │   ├── Board.tsx       # Pins display
│       │   ├── SeatDeck.tsx    # Presence
│       │   └── ...
│       ├── api/                # HTTP client
│       │   ├── client.ts
│       │   ├── tables.ts
│       │   └── sayings.ts
│       ├── hooks/
│       │   └── useLongPoll.ts
│       ├── rendering/          # Markdown + Mermaid
│       │   ├── markdown.tsx
│       │   ├── mermaid.tsx
│       │   └── math.tsx
│       ├── security/               # (stub svg_sanitize.ts removed — see svg-sanitizer.ts in rendering/)
│       └── styles/
│           └── ...
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/                   # Core unit tests
│   │   ├── __init__.py
│   │   ├── test_table.py
│   │   ├── test_saying.py
│   │   └── test_mention.py
│   └── integration/            # Shell integration tests
│       ├── __init__.py
│       ├── test_api.py
│       └── test_mcp.py
├── data/                       # SQLite database (gitignored)
│   └── .gitkeep
├── docs/                       # Specification documents
├── .invar/                     # Invar configuration
│   ├── context.md
│   └── examples/
├── .opencode/                  # OpenCode configuration
│   └── agents/
├── .gitignore
├── CLAUDE.md
├── AGENTS.md -> .agents/instructions.md
└── README.md
```

## Directory Responsibilities

| Directory      | Responsibility                      | Invar Zone |
| -------------- | ----------------------------------- | ---------- |
| `src/tasca/core/`    | Pure business logic, contracts      | Core       |
| `src/tasca/shell/`   | I/O, storage, API, MCP              | Shell      |
| `web/`               | Frontend SPA                        | (excluded) |
| `tests/`             | Unit + integration tests            | —          |
| `data/`              | SQLite database files               | —          |

## Key Files

| File                       | Purpose                                       |
| -------------------------- | --------------------------------------------- |
| `pyproject.toml`           | Python deps, Invar config, project metadata   |
| `src/tasca/main.py`        | FastAPI app + MCP server startup              |
| `src/tasca/config.py`      | Environment variables, settings               |
| `src/tasca/shell/api/`     | REST endpoints (`/api/v1/...`)                |
| `src/tasca/shell/mcp/`     | MCP tools (`tasca.table.*`, `tasca.seat.*`)   |
| `src/tasca/shell/storage/` | SQLite repositories                           |
| `web/vite.config.ts`       | Vite config + dev proxy to FastAPI            |

## Database Location

- Default: `./data/tasca.db`
- Override: `TASCA_DB_PATH` environment variable
- WAL mode enabled
- Single-writer constraint (v0.1)

## Frontend Build Output

- Development: Vite dev server with proxy to `localhost:8000`
- Production: `vite build` outputs to `web/dist/`, served by FastAPI

## Invar Configuration

```toml
[tool.invar.guard]
core_paths = ["src/tasca/core"]
shell_paths = ["src/tasca/shell"]
exclude = ["web/", "tests/"]
```

Frontend (`web/`) is excluded from Invar guard — only backend Python code is verified.

## MCP + REST Dual Interface
Both interfaces share shell-application operations where behavior needs I/O and reusable ownership. Transport adapters keep their protocol-specific contracts:

```
                    ┌─────────────────┐
                    │   Core Services │
                    │ (pure domain +  │
                    │  formatting)    │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ Shell Operations│
                    │ services/...    │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
    ┌─────────▼─────────┐       ┌──────────▼──────────┐
    │    REST API       │       │     MCP Server      │
    │ auth/status/models│       │ tool schemas/envelopes│
    │ /api/v1/...      │       │  table_*/seat_*      │
    └───────────────────┘       └─────────────────────┘
              │                             │
              ▼                             ▼
    Web UI / HTTP clients          Claude/OpenCode/Cursor (MCP)
```

REST routes and MCP entrypoints are not duplicate business-logic owners; they adapt shared shell-operation outcomes into transport-local request, response, auth, and error shapes.
## MCP Tool to REST Endpoint Mapping
| MCP Tool                | REST Endpoint / HTTP Binding                  |
| ----------------------- | --------------------------------------------- |
| `tasca.table.create`    | `POST /api/v1/tables`                         |
| `tasca.table.join`      | `POST /api/v1/tables/join`                    |
| `tasca.table.get`       | `GET /api/v1/tables/{table_id}`               |
| `tasca.table.update`    | `PUT /api/v1/tables/{table_id}?expected_version=N` |
| `tasca.table.control`   | `POST /api/v1/tables/{table_id}/control`      |
| `tasca.table.delete_batch` | `POST /api/v1/tables/actions/batch-delete` |
| `tasca.table.say`       | `POST /api/v1/tables/{table_id}/sayings`      |
| `tasca.table.listen`    | `GET /api/v1/tables/{table_id}/sayings`       |
| `tasca.table.wait`      | `GET /api/v1/tables/{table_id}/sayings/wait?timeout=<seconds>` |
| `tasca.table.export`    | `GET /api/v1/tables/{table_id}/export/{jsonl|markdown}` |
| `tasca.seat.heartbeat`  | `POST /api/v1/tables/{table_id}/seats/heartbeat` |
| `tasca.seat.list`       | `GET /api/v1/tables/{table_id}/seats`         |
| `tasca.patron.register` | `POST /api/v1/patrons`                        |
| `tasca.patron.get`      | `GET /api/v1/patrons/{patron_id}`             |

Note: the mapping is semantic, not envelope-identical. MCP runtime tools use unprefixed FastMCP names such as `table_create`; `tool_contracts.py` records their conceptual `tasca.*` names and defaults.