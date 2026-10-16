#!/bin/bash
set -e

# Start the FastAPI application in the foreground so it is the supervised
# process (PID 1), receives SIGTERM/SIGINT for graceful shutdown, and the
# container exits with the app's status code (enabling restart policies).
exec uvicorn app.main:app \
    --host "${SERENDIPITY_WEBUI_HOST:-127.0.0.1}" \
    --port "${SERENDIPITY_WEBUI_PORT:-8080}"
