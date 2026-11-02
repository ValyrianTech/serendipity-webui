#!/bin/bash
set -e

# Start the FastAPI application in the foreground so it is the supervised
# process (PID 1), receives SIGTERM/SIGINT for graceful shutdown, and the
# container exits with the app's status code (enabling restart policies).

# Resolve the effective bind host and port once. Inside a container, loopback
# (127.0.0.1) is only reachable from within the container's own network
# namespace, so the default here is 0.0.0.0 (all interfaces) to make
# `docker run -p 8080:8080` work out of the box. Operators who want a
# loopback-only bind can set SERENDIPITY_WEBUI_HOST=127.0.0.1 explicitly.
SERENDIPITY_HOST="${SERENDIPITY_WEBUI_HOST:-0.0.0.0}"
SERENDIPITY_PORT="${SERENDIPITY_WEBUI_PORT:-8080}"

# Export the resolved values so app.main's module-level WEBUI_HOST/WEBUI_PORT
# (read from SERENDIPITY_WEBUI_HOST/SERENDIPITY_WEBUI_PORT at import time)
# match the address uvicorn actually binds. Without this, the --host flag alone
# would leave the app believing it is bound to 127.0.0.1, populating a useless
# http://127.0.0.1:PORT startup allow-list entry instead of correctly leaving
# the default empty for a wildcard bind.
#
# Note: a wildcard bind (0.0.0.0) leaves the startup TTS allow-list empty, so
# operators exposing the container should set SERENDIPITY_WEBUI_ALLOWED_ORIGINS
# explicitly. The exported env var takes precedence over any .env file because
# load_dotenv does not override existing environment variables.
export SERENDIPITY_WEBUI_HOST="$SERENDIPITY_HOST"
export SERENDIPITY_WEBUI_PORT="$SERENDIPITY_PORT"

exec uvicorn app.main:app \
    --host "$SERENDIPITY_HOST" \
    --port "$SERENDIPITY_PORT"
