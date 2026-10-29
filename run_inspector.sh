#!/usr/bin/env bash

PROJECT_DIR="$HOME/projects/odoo-developer-assistant"
cd "$PROJECT_DIR"
source "$PROJECT_DIR/.venv/bin/activate"

echo "Starting MCP Inspector..."

setsid mcp dev server.py &
INSPECTOR_PID=$!

cleanup() {
    trap - INT TERM EXIT

    echo
    echo "Stopping MCP Inspector..."

    kill -TERM -- "-$INSPECTOR_PID" 2>/dev/null || true

    for i in {1..30}; do
        if ! kill -0 "$INSPECTOR_PID" 2>/dev/null; then
            break
        fi
        sleep 0.1
    done

    kill -KILL -- "-$INSPECTOR_PID" 2>/dev/null || true
    wait "$INSPECTOR_PID" 2>/dev/null || true

    echo "Cleanup finished."
}

trap cleanup INT TERM EXIT

wait "$INSPECTOR_PID"
