# E2E Testing Guide

This document describes how to run end-to-end (E2E) tests that require an external
server, particularly for MCP proxy mode testing.

## Overview

Some integration tests require a running Tasca server because they test features
that cannot be adequately tested with in-process ASGI fixtures:

- **MCP Proxy Mode**: Tests tool forwarding between local and remote MCP servers
- **Real HTTP Transport**: Tests actual network behavior (timeouts, connection handling)
- **Multi-Server Scenarios**: Tests interactions between multiple server instances

## Quick Start

### Option 1: Using the helper script (recommended)

```bash
# Run all E2E proxy tests
./scripts/run-e2e-external-server.sh

# Run with verbose output
./scripts/run-e2e-external-server.sh -v

# Run specific test file
./scripts/run-e2e-external-server.sh tests/integration/test_mcp_proxy.py

# Run specific test
./scripts/run-e2e-external-server.sh tests/integration/test_mcp_proxy.py::test_e2e_proxy_mode_table_list_forwarding
```

### Option 2: Manual execution

```bash
# Terminal 1: Start the server
uv run tasca

# Terminal 2: Run tests
TASCA_USE_EXTERNAL_SERVER=1 uv run pytest tests/integration/test_mcp_proxy.py -v
```

### Option 3: With custom server URL

```bash
# Start server on custom port
TASCA_PORT=9000 uv run tasca

# Run tests pointing to custom URL
TASCA_USE_EXTERNAL_SERVER=1 \
TASCA_TEST_MCP_URL=http://localhost:9000/mcp \
uv run pytest tests/integration/test_mcp_proxy.py -v
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TASCA_USE_EXTERNAL_SERVER` | (unset) | Set to `1`, `true`, or `yes` to enable external server mode |
| `TASCA_TEST_API_URL` | `http://localhost:8000` | Base URL for REST API |
| `TASCA_TEST_MCP_URL` | `{API_URL}/mcp` | Base URL for MCP HTTP endpoint |
| `TASCA_TEST_TIMEOUT` | `30` | Request timeout in seconds |
| `TASCA_PORT` | `8000` | Server port (used by `tasca` command) |

## Test Categories

### MCP Proxy Mode Tests (`test_mcp_proxy.py`)

These tests verify the MCP proxy functionality where a local client can forward
tool calls to an upstream server.

| Test | Description |
|------|-------------|
| `test_e2e_proxy_mode_table_list_forwarding` | Verifies table_list is forwarded correctly |
| `test_e2e_proxy_mode_connect_disconnect_cycle` | Tests multiple mode switches |
| `test_e2e_proxy_mode_table_operations` | Tests CRUD through proxy |
| `test_e2e_proxy_mode_local_tools_not_forwarded` | Ensures local-only tools work in remote mode |
| `test_e2e_proxy_mode_no_upstream_url_error` | Tests error handling for unreachable upstream |
| `test_e2e_proxy_mode_data_isolation` | Verifies mode switching actually changes backend |

### Tests That Don't Require External Server

Most integration tests use in-process ASGI testing via `TestClient` and do NOT
require an external server:

```bash
# These work without TASCA_USE_EXTERNAL_SERVER
pytest tests/integration/test_mcp.py -v -k "not stdio"
pytest tests/integration/test_api.py -v
```

## CI Integration

For CI environments, use background process management:

```yaml
# GitHub Actions example
- name: Start Tasca server
  run: |
    uv run tasca &
    echo $! > /tmp/tasca.pid
    sleep 5  # Wait for server to be ready

- name: Run E2E tests
  env:
    TASCA_USE_EXTERNAL_SERVER: "1"
  run: uv run pytest tests/integration/test_mcp_proxy.py -v

- name: Stop Tasca server
  run: kill $(cat /tmp/tasca.pid) || true
```

## Troubleshooting

### Server not ready

**Error**: Tests fail with connection refused

**Solution**: Ensure server is running and ready before tests:
```bash
# Wait for server to be ready
./scripts/run-e2e-external-server.sh --wait
```

### Port already in use

**Error**: `Address already in use` when starting server

**Solution**: Kill existing process or use different port:
```bash
# Find and kill process using port 8000
lsof -i :8000 | grep LISTEN | awk '{print $2}' | xargs kill

# Or use different port
TASCA_PORT=8001 uv run tasca &
TASCA_USE_EXTERNAL_SERVER=1 TASCA_TEST_MCP_URL=http://localhost:8001/mcp pytest tests/integration/test_mcp_proxy.py -v
```

### Tests skipped unexpectedly

**Error**: Tests show `SKIPPED` with message about external server

**Solution**: Ensure `TASCA_USE_EXTERNAL_SERVER=1` is set:
```bash
# Verify environment
echo $TASCA_USE_EXTERNAL_SERVER  # Should print "1"
```

## Test Data Isolation

E2E tests share the same database as the running server. Each test is responsible
for:

1. **Creating its own test data** (tables, patrons, etc.)
2. **Not interfering with other tests** (use unique identifiers)
3. **Not leaving orphaned data** (cleanup if needed)

The `reset_proxy_mode` fixture ensures proxy mode is reset before/after each test.
