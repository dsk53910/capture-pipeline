#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

cleanup() {
    echo ""
    echo "[shutdown] stopping server..."
    if [ -n "${SERVER_PID:-}" ]; then
        kill $SERVER_PID 2>/dev/null || true
    fi
    # Fallback: kill anything on port 8730
    local pid=$(lsof -ti :8730 2>/dev/null)
    if [ -n "$pid" ]; then
        kill -TERM $pid 2>/dev/null || true
        sleep 0.5
        kill -KILL $pid 2>/dev/null || true
    fi
    echo "[shutdown] done"
}
trap cleanup EXIT INT TERM

# Kill any stale server
lsof -ti :8730 | xargs kill -9 2>/dev/null || true

# Start server in background (capture python PID, not uv)
uv run python pipeline_server.py &
SERVER_PID=$!
echo "[start] server PID=$SERVER_PID"

# Wait for server
for i in {1..10}; do
    if curl -s http://127.0.0.1:8730/status > /dev/null 2>&1; then
        echo "[start] server ready"
        break
    fi
    sleep 0.5
done

# Launch TUI (blocking)
uv run python pipeline_tui.py
