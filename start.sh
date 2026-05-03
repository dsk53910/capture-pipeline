#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

cleanup() {
    echo ""
    echo "[shutdown] stopping server..."
    kill $SERVER_PID 2>/dev/null || true
    wait $SERVER_PID 2>/dev/null || true
    echo "[shutdown] done"
}
trap cleanup EXIT INT TERM

# Start server in background
uv run python pipeline_server.py &
SERVER_PID=$!
echo "[start] server PID=$SERVER_PID"

# Wait for server to be ready
for i in {1..10}; do
    if curl -s http://127.0.0.1:8730/status > /dev/null 2>&1; then
        echo "[start] server ready"
        break
    fi
    sleep 0.5
done

# Launch TUI (blocking)
uv run python pipeline_tui.py

# Cleanup happens via trap
