#!/bin/bash
set -e

# ---------------------------------------------------------------------------
# commaBot Docker entrypoint
#
# Starts the Letta Code server (with GitHub channel adapter) and the Flask
# webhook listener. The server runs in background; Flask runs in foreground.
# ---------------------------------------------------------------------------

# Start Letta Code server with GitHub channel adapter
# --debug: plain-text logs (no interactive UI) for background operation
cd /app/sdk
letta server --channels github --env-name commabot --debug &
SERVER_PID=$!

# Ensure the server is cleaned up when Flask exits
cleanup() {
    echo "Shutting down..."
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for the channel adapter's HTTP server to be ready
echo "Waiting for Letta Code server + GitHub channel adapter to start..."
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:3000/health > /dev/null 2>&1; then
        echo "GitHub channel adapter is ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "WARNING: Channel adapter did not become ready within 30s"
    fi
    sleep 1
done

# Run recovery script (non-fatal — it catches up on missed GitHub deliveries)
cd /app
python3 recovery.py || true

# Start webhook listener (foreground — this is the main process)
exec python3 webhook-listener.py
