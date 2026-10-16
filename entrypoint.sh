#!/bin/bash
set -e

# Start the FastAPI application in the background
uvicorn app.main:app --host "${SERENDIPITY_WEBUI_HOST:-127.0.0.1}" --port "${SERENDIPITY_WEBUI_PORT:-8080}" &

# Keep container running
sleep infinity
