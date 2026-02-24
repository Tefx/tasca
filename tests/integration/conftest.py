"""
Integration test fixtures and configuration.

This module provides fixtures for testing both REST API and MCP endpoints.
All base URLs are configurable via environment variables for flexibility.

Environment Variables:
    TASCA_TEST_API_URL: Base URL for REST API (default: http://localhost:8000)
    TASCA_TEST_MCP_URL: Base URL for MCP HTTP endpoint (default: http://localhost:8000/mcp)
    TASCA_TEST_TIMEOUT: Request timeout in seconds (default: 30)

Usage:
    # Run HTTP integration tests with in-process ASGI (no external server needed)
    pytest tests/integration/test_mcp.py -v -k "not stdio"

    # Run MCP STDIO tests (uses tasca-mcp command directly)
    pytest tests/integration/test_mcp.py -v -k stdio

    # Run with custom external URL (requires running server)
    TASCA_TEST_API_URL=http://api.example.com pytest tests/integration/

================================================================================
TEST SERVER CLEANUP ARCHITECTURE
================================================================================

The upstream_server fixture spawns external subprocess processes for E2E tests.
Proper cleanup is CRITICAL to avoid leaking processes and occupied ports.

Cleanup Layers (in order of invocation):
    1. Context Manager (_server_lifecycle) — guaranteed cleanup via finally block
    2. Signal Handlers (SIGTERM, SIGINT) — graceful shutdown on interrupt
    3. atexit Handler (_emergency_cleanup) — last-resort cleanup on interpreter exit

Cleanup Sequence (_cleanup_server_process):
    Step 1: proc.terminate() — send SIGTERM for graceful shutdown
            Wait up to timeout_terminate (default: 5s)
            ↓
    Step 2: proc.kill() — send SIGKILL if terminate times out
            Wait up to timeout_kill (default: 2s)
            ↓
    Step 3: Port verification — poll until port released (max 2s)
            Warn if port still occupied (non-blocking)

Error Scenarios and Recovery:
    ┌─────────────────────────────┬─────────────────────────────────────────┐
    │ Scenario                    │ Recovery Action                         │
    ├─────────────────────────────┼─────────────────────────────────────────┤
    │ Process hangs on SIGTERM    │ Force SIGKILL after timeout_terminate   │
    │ Process unkillable (zombie) │ Log warning, let OS reap                  │
    │ Port still bound            │ Warn but continue (non-blocking)        │
    │ Interpreter crash           │ atexit handler performs emergency kill  │
    │ SIGINT/SIGTERM received     │ Signal handler cleans all processes     │
    └─────────────────────────────┴─────────────────────────────────────────┘

Port Occupation Troubleshooting:
    Symptom: "Port XXXXX may still be in use after cleanup" warning

    Diagnosis:
        # Check if port is still bound
        lsof -i :<PORT>
        netstat -an | grep <PORT>

    Common causes:
        1. Server process didn't close connections gracefully
           → Increase timeout_terminate in _server_lifecycle call
        2. OS TCP TIME_WAIT state (normal, clears in 30-60s)
           → Use SO_REUSEADDR in server (not in test control)
        3. Zombie process holding port
           → `kill -9 <PID>` manually, or wait for OS reaping

    Cleanup verification:
        # Kill any orphaned test processes
        pkill -f "tasca.*new.*proxy-e2e-test"

        # Find processes listening on test ports
        lsof -i -P | grep LISTEN | grep tasca

Cleanup Verification in Tests:
    Tests using upstream_server fixture can verify clean state:

        def test_something(upstream_server):
            port = upstream_server['port']
            # ... test logic ...
            # Fixture automatically cleans up after test

        # After all tests in module complete, verify no port leaks:
        # (add to test file if needed)
        def test_no_port_leak():
            import socket
            for port in RANGE_OF_TEST_PORTS:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                assert sock.connect_ex(('127.0.0.1', port)) != 0
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypedDict

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI
    from starlette.testclient import TestClient

# Known test token injected via fixture — never changes per test run.
# This value is used in auth tests to guarantee auth is always enforced.
TEST_ADMIN_TOKEN = "test-admin-token-fixture"

# =============================================================================
# Configuration
# =============================================================================

# Base URLs - configurable via environment variables
API_BASE_URL = os.environ.get("TASCA_TEST_API_URL", "http://localhost:8000")
# MCP endpoint is at /mcp
MCP_BASE_URL = os.environ.get("TASCA_TEST_MCP_URL", f"{API_BASE_URL}/mcp")
REQUEST_TIMEOUT = int(os.environ.get("TASCA_TEST_TIMEOUT", "30"))

# Environment variable to force external server (skip ASGI fixture)
USE_EXTERNAL_SERVER = os.environ.get("TASCA_USE_EXTERNAL_SERVER", "").lower() in (
    "1",
    "true",
    "yes",
)


# =============================================================================
# Configuration Fixtures
# =============================================================================


@pytest.fixture
def api_base_url() -> str:
    """Base URL for REST API endpoints.

    Override with TASCA_TEST_API_URL environment variable.

    Returns:
        Base URL string (e.g., "http://localhost:8000")
    """
    return API_BASE_URL


@pytest.fixture
def mcp_base_url() -> str:
    """Base URL for MCP HTTP endpoint.

    Override with TASCA_TEST_MCP_URL environment variable.
    Defaults to {API_BASE_URL}/mcp.

    Returns:
        Base URL string (e.g., "http://localhost:8000/mcp")
    """
    return MCP_BASE_URL


@pytest.fixture
def request_timeout() -> int:
    """Request timeout in seconds.

    Override with TASCA_TEST_TIMEOUT environment variable.

    Returns:
        Timeout in seconds (default: 30)
    """
    return REQUEST_TIMEOUT


@pytest.fixture(autouse=True)
def fixture_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a known admin_token value for every integration test.

    This fixture is autouse so it applies to every test in this package
    without requiring explicit use. It patches the module-level settings
    singleton so that both the app middleware and test code see the same
    known token value.

    Scope: function (default) — resets after every test, no leakage.
    """
    import tasca.config as config_module

    monkeypatch.setattr(config_module.settings, "admin_token", TEST_ADMIN_TOKEN)


# =============================================================================
# ASGI Application Fixtures
# =============================================================================


@pytest.fixture
def asgi_app() -> "FastAPI":
    """Create a FastAPI app instance for ASGI testing.

    This fixture creates the application without starting a server,
    allowing httpx to test it directly via ASGI transport.

    Note: For MCP tests that require lifespan (Streamable HTTP transport),
    use the mcp_test_client fixture instead, which uses Starlette TestClient
    to properly handle FastMCP's task group initialization.

    Returns:
        FastAPI app instance
    """
    from tasca.shell.api.app import create_app

    return create_app()


@pytest.fixture
def mcp_test_client() -> Generator["TestClient", None, None]:
    """Create a test client for MCP HTTP testing with proper lifespan handling.

    Uses Starlette TestClient which properly handles FastMCP's Streamable HTTP
    transport requirements (task group initialization via lifespan events).

    This fixture MUST be used instead of httpx AsyncClient for MCP HTTP tests,
    as httpx ASGI transport does not trigger ASGI lifespan events.

    Yields:
        TestClient configured for MCP endpoint testing
    """
    from starlette.testclient import TestClient

    from tasca.shell.api.app import create_app

    app = create_app()

    with TestClient(
        app,
        base_url="http://test",
        raise_server_exceptions=True,
    ) as client:
        yield client


# =============================================================================
# HTTP Client Fixtures (REST API)
# =============================================================================


@pytest_asyncio.fixture
async def http_client(asgi_app: "FastAPI") -> AsyncGenerator:
    """Async HTTP client for REST API testing.

    Uses httpx ASGI transport for in-process testing without requiring
    an external server. Set TASCA_USE_EXTERNAL_SERVER=1 to use external URL.

    Provides an httpx.AsyncClient configured with:
    - ASGI transport for in-process testing (default)
    - OR external URL if TASCA_USE_EXTERNAL_SERVER is set
    - Automatic resource cleanup

    Yields:
        httpx.AsyncClient instance
    """
    import httpx

    if USE_EXTERNAL_SERVER:
        # Use external server (requires running server)
        async with httpx.AsyncClient(
            base_url=API_BASE_URL,
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
        ) as client:
            yield client
    else:
        # Use ASGI transport for in-process testing
        transport = httpx.ASGITransport(app=asgi_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
        ) as client:
            yield client


# =============================================================================
# MCP Client Fixtures (HTTP Transport)
# =============================================================================


@pytest_asyncio.fixture
async def mcp_http_client(asgi_app: "FastAPI") -> AsyncGenerator:
    """MCP client fixture - DEPRECATED: Use mcp_test_client instead.

    This fixture is kept for backward compatibility but does not work
    for FastMCP's Streamable HTTP transport. Use mcp_test_client instead.

    Yields:
        httpx.AsyncClient (note: will not work for MCP tests)
    """
    import httpx

    if USE_EXTERNAL_SERVER:
        # Use external server (requires running server)
        async with httpx.AsyncClient(
            base_url=MCP_BASE_URL,
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
        ) as client:
            yield client
    else:
        # Use ASGI transport for in-process testing
        transport = httpx.ASGITransport(app=asgi_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test/mcp",
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
        ) as client:
            yield client


# =============================================================================
# MCP Session Fixture
# =============================================================================


class MCPSession(TypedDict):
    """Initialized MCP session state for use in tests.

    Attributes:
        client: Starlette TestClient bound to the app under test.
        headers: HTTP headers including Accept and mcp-session-id (if present).
        session_id: MCP session ID returned by the server, or None if absent.
    """

    client: "TestClient"
    headers: dict[str, str]
    session_id: str | None


@pytest.fixture
def mcp_session(mcp_test_client: "TestClient") -> Generator[MCPSession, None, None]:
    """Provide an initialized MCP session for HTTP transport tests.

    Sends the MCP initialize request and extracts the session ID so that
    individual test functions do not need to repeat the boilerplate.

    Includes Bearer token authentication if admin_token is configured.

    Args:
        mcp_test_client: Starlette TestClient with proper lifespan handling.

    Yields:
        MCPSession containing the client, initialized headers, and session_id.
    """
    # Build headers with auth — admin_token is always set via fixture_admin_token.
    headers: dict[str, str] = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {TEST_ADMIN_TOKEN}",
    }

    init_response = mcp_test_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "tasca-test-client",
                    "version": "0.1.0",
                },
            },
        },
        headers=headers,
    )
    assert init_response.status_code == 200

    session_id: str | None = init_response.headers.get("mcp-session-id")
    if session_id:
        headers["mcp-session-id"] = session_id

    yield MCPSession(client=mcp_test_client, headers=headers, session_id=session_id)


# =============================================================================
# Upstream Server Fixture (for proxy E2E tests)
# =============================================================================

UPSTREAM_TOKEN = "test-upstream-token"

# =============================================================================
# PROCESS REGISTRY FOR EMERGENCY CLEANUP
# =============================================================================
# Global registry tracks all spawned processes for emergency cleanup.
# This provides a backstop when normal cleanup (context manager) fails.
#
# Tracking mechanism:
#   _register_process() → Called when process starts
#   _unregister_process() → Called when process dies (normal or killed)
#   _spawned_processes → Dict of pid -> ProcessInfo for atexit to iterate
#
# Thread safety: NOT thread-safe. Assumes single-threaded pytest execution.
# If parallel test execution is enabled, this registry would need locking.
#
# CRITICAL: Signal handlers are registered at MODULE LOAD (not in fixtures)
# to ensure cleanup works even on early SIGINT/SIGTERM before tests start.

from dataclasses import dataclass


@dataclass
class ProcessInfo:
    """Tracked process information for cleanup."""

    proc: subprocess.Popen
    port: int
    name: str = "unknown"


_spawned_processes: dict[int, ProcessInfo] = {}
_cleanup_in_progress = False  # Prevent re-entrant cleanup


def _do_kill_process(info: ProcessInfo, timeout_kill: float = 2.0) -> None:
    """Kill a process and wait for it to exit.

    Uses process group kill for reliable termination of all child processes.
    """
    proc = info.proc
    if proc.poll() is not None:
        return  # Already dead

    try:
        # Try process group kill first (catches child processes too)
        # On Windows, this would need different handling
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        # Process group doesn't exist or process already dead
        # Fall back to individual process kill
        try:
            proc.kill()
        except (OSError, ProcessLookupError):
            pass

    try:
        proc.wait(timeout=timeout_kill)
    except subprocess.TimeoutExpired:
        pass  # Zombie process - OS will reap


def _emergency_cleanup() -> None:
    """Emergency cleanup handler for atexit and signal handlers.

    LAST-RESORT SAFETY NET for orphaned processes.

    When this handler runs:
        - Normal pytest cleanup failed (exception, crash, etc.)
        - Interpreter is shutting down (atexit triggered)
        - SIGINT/SIGTERM received during test run
        - Process resources must be freed before exit

    This handler iterates through ALL registered processes and kills them.
    It's called automatically by Python's atexit mechanism, ensuring cleanup
    even if the test fixture's context manager doesn't exit cleanly.

    Safety guarantees:
        - Catches all exceptions (cleanup should never fail)
        - Prevents re-entrant cleanup via _cleanup_in_progress flag
        - Uses process group kill for reliable termination
        - Short timeout for kill wait (we're in interpreter shutdown)
        - Clears registry to prevent double-cleanup attempts

    IMPORTANT: This is not a substitute for proper cleanup in tests.
    It exists to prevent process leaks when things go wrong.
    """
    global _cleanup_in_progress

    if _cleanup_in_progress:
        return  # Prevent re-entrant cleanup

    _cleanup_in_progress = True

    try:
        for pid, info in list(_spawned_processes.items()):
            if info.proc.poll() is None:  # Process still running
                try:
                    _do_kill_process(info, timeout_kill=2.0)
                except Exception:
                    pass  # Best effort in emergency cleanup
        _spawned_processes.clear()
    finally:
        _cleanup_in_progress = False


def _signal_handler(signum: int, frame: object) -> None:
    """Signal handler for graceful shutdown on SIGTERM/SIGINT.

    Triggered when:
        - User hits Ctrl+C (SIGINT)
        - Process receives SIGTERM (e.g., from process manager)
        - CI environment cancels the job

    Sequence:
        1. Run emergency cleanup (kill all spawned processes)
        2. Restore default signal handler
        3. Re-raise signal so Python exits with correct code

    This ensures test processes don't outlive the parent test runner.
    Without this handler, Ctrl+C would leave orphan servers running.
    """
    # Run cleanup - this will kill all tracked processes
    _emergency_cleanup()

    # Restore default handler and re-raise
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _register_signal_handlers() -> None:
    """Register signal handlers for graceful shutdown.

    Called once at module load to ensure handlers are active
    before any tests run.
    """
    # Use signal.SIG_IGN to prevent interruption during handler setup
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)


def _unregister_signal_handlers() -> None:
    """Restore default signal handlers.

    DEPRECATED: This function should NOT be called during tests.
    Signal handlers should remain active for the entire session.
    Kept for compatibility but does nothing.
    """
    # Intentionally do nothing - handlers should remain active
    pass


# Register signal handlers at module load (BEFORE any tests run)
# This ensures cleanup works even on early SIGINT/SIGTERM
_register_signal_handlers()

# Register atexit handler at module load
atexit.register(_emergency_cleanup)


@contextmanager
def _server_lifecycle(
    proc: subprocess.Popen,
    port: int,
    timeout_terminate: float = 5.0,
    timeout_kill: float = 2.0,
) -> Generator[subprocess.Popen, None, None]:
    """Context manager for robust server process lifecycle.

    GUARANTEED CLEANUP via finally block - covers all exception paths:
        - Normal exit (yield returns)
        - Test failure (assertion error)
        - Unexpected exception
        - Early return from test

    This is the PRIMARY cleanup mechanism for spawned processes.
    Signal handlers and atexit are backstops for abnormal termination.

    Timeline:
        ┌─────────────────────────────────────────────────────┐
        │ Test function body                                  │
        │ with _server_lifecycle(proc, port):                 │
        │     yield proc  ← Test uses the server              │
        │     # ... test code ...                             │
        │                                                     │
        │ # Any exit path triggers finally block              │
        │ finally:                                            │
        │     _cleanup_server_process(proc, port, ...)        │
        │     # → terminate → kill → verify port              │
        └─────────────────────────────────────────────────────┘

    Args:
        proc: The subprocess.Popen instance to manage.
        port: The port the server is listening on (for verification).
        timeout_terminate: Seconds to wait after SIGTERM before SIGKILL.
        timeout_kill: Seconds to wait after SIGKILL before giving up.

    Yields:
        The process instance.
    """
    try:
        yield proc
    finally:
        # GUARANTEED: always runs, even on exception/early return
        _cleanup_server_process(proc, port, timeout_terminate, timeout_kill)


def _cleanup_server_process(
    proc: subprocess.Popen,
    port: int,
    timeout_terminate: float = 5.0,
    timeout_kill: float = 2.0,
) -> None:
    """Clean up a server process with verification.

    Uses graceful termination first, then force kill with timeouts.
    Verifies port is released after cleanup.

    CLEANUP SEQUENCE (see module docstring for architecture overview):

    Phase 1: Graceful Termination (SIGTERM)
        - Sends terminate signal allowing process to close connections
        - Uses process group kill if available (catches child processes)
        - Waits up to timeout_terminate (default: 5s) for clean exit
        - Most servers exit cleanly here, releasing ports immediately

    Phase 2: Force Kill (SIGKILL)
        - Triggered if terminate times out (hung process)
        - Unconditionally kills process via OS signal
        - Process may not close connections properly → port may linger

    Phase 3: Port Release Verification
        - Polls port availability via non-blocking connect
        - Waits up to max_attempts * delay (default: 2s total)
        - Warns if port still bound (non-blocking for test reliability)
        - Port may remain in TIME_WAIT state due to OS TCP protocol

    Error Handling:
        - All exceptions caught to ensure cleanup continues
        - Unregister process from emergency tracking after any outcome
        - Never raises - cleanup should be idempotent and safe
    """
    # Early exit: process already dead
    if proc.poll() is not None:
        # Process already terminated - just unregister from tracking
        _unregister_process(proc)
        return

    # ─────────────────────────────────────────────────────────────────────
    # PHASE 1: Graceful Termination (SIGTERM via process group)
    # ─────────────────────────────────────────────────────────────────────
    # Preferred method - allows server to close connections properly
    # Use process group for reliable termination of all child processes
    try:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            # Fall back to individual process terminate
            proc.terminate()
        proc.wait(timeout=timeout_terminate)
    except subprocess.TimeoutExpired:
        # Server didn't respond to SIGTERM in time - it's hung
        # Fall through to Phase 2: force kill
        # ─────────────────────────────────────────────────────────────────
        # PHASE 2: Force Kill (SIGKILL via process group)
        # ─────────────────────────────────────────────────────────────────
        # SIGKILL cannot be caught - OS forcibly terminates process
        # Downside: connections may not close cleanly, port may linger
        try:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                proc.kill()
            proc.wait(timeout=timeout_kill)
        except subprocess.TimeoutExpired:
            # Extremely rare: zombie process that won't die
            # OS will reap eventually; we've done our best
            pass
    except Exception:
        # Unexpected error during terminate (e.g., permission denied)
        # Try force kill as last resort before giving up
        try:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                proc.kill()
            proc.wait(timeout=timeout_kill)
        except Exception:
            pass  # Best effort - don't crash the test suite

    # Remove from emergency cleanup tracking (process is dead or unkillable)
    _unregister_process(proc)

    # ─────────────────────────────────────────────────────────────────────
    # PHASE 3: Port Release Verification
    # ─────────────────────────────────────────────────────────────────────
    # Poll until port is free or we give up (non-blocking for test reliability)
    _verify_port_released(port, max_attempts=20, delay=0.1)


def _register_process(proc: subprocess.Popen, port: int, name: str = "server") -> None:
    """Register a process for emergency cleanup tracking.

    Args:
        proc: The subprocess to track.
        port: The port the server is listening on.
        name: Human-readable name for logging/debugging.
    """
    _spawned_processes[proc.pid] = ProcessInfo(proc=proc, port=port, name=name)


def _unregister_process(proc: subprocess.Popen) -> None:
    """Unregister a process from emergency cleanup tracking."""
    _spawned_processes.pop(proc.pid, None)


def _verify_port_released(port: int, max_attempts: int = 20, delay: float = 0.1) -> None:
    """Verify that a port has been released.

    NON-BLOCKING: Warns if port still occupied but does NOT fail the test.
    This is intentional - port occupation is often transient and tests should
    not fail due to OS TCP state (TIME_WAIT) that clears naturally.

    How it works:
        1. Creates a TCP socket
        2. Attempts non-blocking connect to 127.0.0.1:port
        3. If connection refused (ECONNREFUSED) → port is free
        4. If connection succeeds → port still in use, retry
        5. After max_attempts, warn and return

    Why ports may remain occupied after process kill:
        ┌──────────────────────────────────────────────────────────────┐
        │ Scenario              │ Cause                    │ Duration   │
        ├──────────────────────────────────────────────────────────────┤
        │ TIME_WAIT state       │ OS TCP protocol          │ 30-60s     │
        │ Zombie process        │ Process not reaped       │ Indefinite │
        │ Socket linger         │ Server SO_LINGER option  │ Configured │
        │ Multiple listeners    │ SO_REUSEPORT in use      │ Immediate  │
        └──────────────────────────────────────────────────────────────┘

    Troubleshooting port occupation:
        # Check what's using the port
        lsof -i :<PORT>

        # Force kill any remaining processes
        kill -9 $(lsof -t -i :<PORT>)

        # Wait for OS to clear TIME_WAIT (or use SO_REUSEADDR in server)

    Args:
        port: The port to check.
        max_attempts: Maximum number of connection attempts.
        delay: Delay between attempts in seconds.
    """
    import socket
    import time

    for _ in range(max_attempts):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.1)
        try:
            # If connect succeeds, port is still in use
            result = sock.connect_ex(("127.0.0.1", port))
            if result != 0:
                # Connection refused - port is free
                return
        except OSError:
            # Port is free
            return
        finally:
            sock.close()
        time.sleep(delay)

    # Port still in use after max attempts - log warning but don't fail
    # (this is best-effort verification)
    import warnings

    warnings.warn(
        f"Port {port} may still be in use after cleanup (max attempts exceeded)",
        RuntimeWarning,
        stacklevel=2,
    )


@pytest.fixture(scope="module")
def upstream_server(tmp_path_factory: pytest.TempPathFactory):
    """Start a real upstream tasca server as a subprocess for proxy E2E tests.

    Uses subprocess isolation to avoid module-level singleton conflicts
    (_config, mcp) that cause self-referencing proxy loops when running
    two instances in the same process.

    CLEANUP ARCHITECTURE:
    See module docstring for detailed cleanup layer documentation.

        Layer 1: _server_lifecycle context manager (primary)
            └── Guaranteed via finally block
        Layer 2: Signal handlers (SIGTERM, SIGINT)
            └── Triggered on user interrupt
        Layer 3: atexit handler (_emergency_cleanup)
            └── Triggered on interpreter shutdown

    Fixture Timeline:
        1. Find free port via socket bind
        2. Spawn subprocess with tasca CLI
        3. Register process for emergency cleanup
        4. Poll until server healthy (max 10s)
        5. Enter context manager for guaranteed cleanup
        6. Yield server config to test
        7. Test runs...
        8. Context manager exits → cleanup triggered
        9. Process terminated, port verified

    Yields:
        Dict with 'url' (MCP endpoint), 'token', and 'port'.

    Raises:
        RuntimeError: Server fails to start within 10s timeout.
    """
    import socket
    import time

    import httpx

    # Find a free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    db_path = tmp_path_factory.mktemp("upstream") / "upstream.db"

    proc = None
    try:
        proc = subprocess.Popen(
            [
                "uv",
                "run",
                "tasca",
                "new",
                "proxy-e2e-test",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env={
                **os.environ,
                "TASCA_DB_PATH": str(db_path),
                "TASCA_ADMIN_TOKEN": UPSTREAM_TOKEN,
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Start new process group for reliable cleanup of child processes
            # This allows us to kill the entire process tree via os.killpg()
            start_new_session=True,
        )

        # Register for emergency cleanup (signal handlers already registered at module load)
        _register_process(proc, port, name="upstream-server")

        # Poll until server is ready (max 10s)
        base_url = f"http://127.0.0.1:{port}"
        ready = False
        for _ in range(100):
            try:
                r = httpx.get(f"{base_url}/api/v1/health", timeout=1)
                if r.status_code == 200:
                    ready = True
                    break
            except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
                pass
            time.sleep(0.1)

        if not ready:
            # Cleanup before raising error
            _cleanup_server_process(proc, port)
            stdout = proc.stdout.read().decode() if proc.stdout else ""
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(
                f"Upstream server failed to start on port {port}.\n"
                f"stdout: {stdout[:500]}\nstderr: {stderr[:500]}"
            )

        with _server_lifecycle(proc, port):
            yield {"url": f"{base_url}/mcp", "token": UPSTREAM_TOKEN, "port": port}

    except Exception:
        # If an exception occurred before context manager entry, clean up manually
        if proc is not None and proc.poll() is None:
            _cleanup_server_process(proc, port)
        raise


# =============================================================================
# Test Data Fixtures
# =============================================================================


@pytest.fixture
def sample_patron_data() -> dict:
    """Sample patron data for testing.

    Returns:
        Dictionary with patron fields
    """
    return {
        "patron_id": "test-patron-001",
        "display_name": "Test Agent",
        "alias": "testagent",
        "meta": {"test": True},
    }


@pytest.fixture
def sample_table_data() -> dict:
    """Sample table data for testing.

    Returns:
        Dictionary with table creation fields
    """
    return {
        "created_by": "test-patron-001",
        "title": "Test Discussion Table",
        "host_ids": ["test-patron-001"],
        "metadata": {"topic": "testing"},
    }


@pytest.fixture
def sample_saying_data() -> dict:
    """Sample saying data for testing.

    Returns:
        Dictionary with saying creation fields
    """
    return {
        "content": "Hello from integration test!",
        "patron_id": "test-patron-001",
        "speaker_kind": "agent",
        "saying_type": "text",
    }


# =============================================================================
# E2E External Server Fixtures
# =============================================================================


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "e2e_external: Tests that require an external server (set TASCA_USE_EXTERNAL_SERVER=1)",
    )


@pytest.fixture
def external_server_config() -> dict[str, str | int]:
    """Configuration for external server E2E tests.

    Provides the MCP URL and timeout for tests that require an external server.
    Tests using this fixture should be marked with @pytest.mark.e2e_external
    and will be skipped if TASCA_USE_EXTERNAL_SERVER is not set.

    Returns:
        Dictionary with mcp_url, api_url, and timeout keys.

    Example:
        @pytest.mark.e2e_external
        def test_something(external_server_config):
            url = external_server_config["mcp_url"]
            # ... test against external server
    """
    if not USE_EXTERNAL_SERVER:
        pytest.skip("E2E test requires TASCA_USE_EXTERNAL_SERVER=1")
    return {
        "mcp_url": MCP_BASE_URL,
        "api_url": API_BASE_URL,
        "timeout": REQUEST_TIMEOUT,
    }
