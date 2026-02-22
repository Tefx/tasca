#!/usr/bin/env bash
#
# run-e2e-external-server.sh - Run E2E tests with an external server
#
# This script:
# 1. Starts a Tasca server in the background
# 2. Waits for it to be ready
# 3. Runs the specified tests with TASCA_USE_EXTERNAL_SERVER=1
# 4. Cleans up the server on exit
#
# Usage:
#   ./scripts/run-e2e-external-server.sh                    # Run all proxy tests
#   ./scripts/run-e2e-external-server.sh -v                 # Verbose mode
#   ./scripts/run-e2e-external-server.sh <test_path>        # Run specific test
#   ./scripts/run-e2e-external-server.sh --wait             # Just wait for server
#   ./scripts/run-e2e-external-server.sh --stop             # Stop running server
#
# Environment Variables:
#   TASCA_PORT           - Server port (default: 8000)
#   TASCA_TEST_TIMEOUT   - Test timeout in seconds (default: 30)
#   EXTRA_PYTEST_ARGS    - Additional pytest arguments
#

set -euo pipefail

# Configuration
TASCA_PORT="${TASCA_PORT:-8000}"
TASCA_TEST_TIMEOUT="${TASCA_TEST_TIMEOUT:-30}"
SERVER_URL="http://localhost:${TASCA_PORT}"
MCP_URL="${SERVER_URL}/mcp"
SERVER_PID=""
SERVER_LOG=""
STARTUP_TIMEOUT=30

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

cleanup() {
    if [[ -n "${SERVER_PID}" ]]; then
        log_info "Stopping server (PID: ${SERVER_PID})..."
        kill "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
        SERVER_PID=""
    fi
    if [[ -n "${SERVER_LOG}" && -f "${SERVER_LOG}" ]]; then
        # Show last 20 lines of server log if there was an error
        if [[ "${CLEANUP_EXIT_CODE:-0}" -ne 0 ]]; then
            log_warn "Server log (last 20 lines):"
            tail -20 "${SERVER_LOG}"
        fi
        rm -f "${SERVER_LOG}"
    fi
}

trap cleanup EXIT

wait_for_server() {
    local max_attempts=$((STARTUP_TIMEOUT * 2))
    local attempt=0
    
    log_info "Waiting for server at ${SERVER_URL}..."
    
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf "${SERVER_URL}/api/v1/health" >/dev/null 2>&1; then
            log_info "Server is ready!"
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 0.5
    done
    
    log_error "Server failed to start within ${STARTUP_TIMEOUT} seconds"
    return 1
}

start_server() {
    SERVER_LOG=$(mktemp)
    
    log_info "Starting Tasca server on port ${TASCA_PORT}..."
    
    # Use a known admin token for test authentication
    # Tests import this token from tests/integration/conftest.py: TEST_ADMIN_TOKEN
    export TASCA_ADMIN_TOKEN="test-admin-token-fixture"
    
    # Start server in background
    TASCA_PORT="${TASCA_PORT}" uv run tasca > "${SERVER_LOG}" 2>&1 &
    SERVER_PID=$!
    
    log_info "Server started (PID: ${SERVER_PID})"
    
    # Wait for server to be ready
    if ! wait_for_server; then
        log_error "Server log:"
        cat "${SERVER_LOG}"
        return 1
    fi
}

stop_server() {
    # Find and kill any existing server on the port
    local pid
    pid=$(lsof -ti:"${TASCA_PORT}" 2>/dev/null || true)
    
    if [[ -n "${pid}" ]]; then
        log_info "Stopping server on port ${TASCA_PORT} (PID: ${pid})..."
        kill "${pid}" 2>/dev/null || true
        sleep 1
        # Force kill if still running
        if kill -0 "${pid}" 2>/dev/null; then
            kill -9 "${pid}" 2>/dev/null || true
        fi
    else
        log_info "No server found on port ${TASCA_PORT}"
    fi
}

run_tests() {
    local test_path="${1:-tests/integration/test_mcp_proxy.py}"
    local pytest_args="${EXTRA_PYTEST_ARGS:-}"
    
    # Check for -v flag
    if [[ "$*" == *"-v"* ]] || [[ "$*" == *"--verbose"* ]]; then
        pytest_args="${pytest_args} -v"
    fi
    
    log_info "Running tests: ${test_path}"
    
    # Export environment variables for tests
    export TASCA_USE_EXTERNAL_SERVER=1
    export TASCA_TEST_API_URL="${SERVER_URL}"
    export TASCA_TEST_MCP_URL="${MCP_URL}"
    export TASCA_TEST_TIMEOUT="${TASCA_TEST_TIMEOUT}"
    
    # Run pytest
    uv run pytest ${pytest_args} "${test_path}"
}

# Parse arguments
ACTION="run"
TEST_PATH=""
PYTEST_ARGS=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --wait)
            ACTION="wait"
            shift
            ;;
        --stop)
            ACTION="stop"
            shift
            ;;
        --help|-h)
            cat << EOF
Usage: $0 [OPTIONS] [TEST_PATH]

Run E2E tests with an external Tasca server.

Options:
    --wait          Start server and wait (don't run tests)
    --stop          Stop any server on the configured port
    -v, --verbose   Verbose pytest output
    -h, --help      Show this help message

Environment:
    TASCA_PORT          Server port (default: 8000)
    TASCA_TEST_TIMEOUT  Test timeout in seconds (default: 30)
    EXTRA_PYTEST_ARGS   Additional pytest arguments

Examples:
    $0                                          # Run all proxy tests
    $0 -v                                       # Run with verbose output
    $0 tests/integration/test_mcp_proxy.py     # Run specific file
    $0 --wait                                   # Just start server
    $0 --stop                                   # Stop server
EOF
            exit 0
            ;;
        -v|--verbose)
            PYTEST_ARGS="${PYTEST_ARGS} -v"
            shift
            ;;
        *)
            # Assume it's a test path
            if [[ -z "${TEST_PATH}" ]]; then
                TEST_PATH="$1"
            else
                PYTEST_ARGS="${PYTEST_ARGS} $1"
            fi
            shift
            ;;
    esac
done

# Default test path
if [[ -z "${TEST_PATH}" ]]; then
    TEST_PATH="tests/integration/test_mcp_proxy.py"
fi

# Execute action
case "${ACTION}" in
    stop)
        stop_server
        ;;
    wait)
        start_server
        log_info "Server is running. Press Ctrl+C to stop."
        # Keep script running until interrupted
        tail -f "${SERVER_LOG}" &
        wait
        ;;
    run)
        start_server
        export EXTRA_PYTEST_ARGS="${PYTEST_ARGS}"
        run_tests "${TEST_PATH}"
        ;;
esac
