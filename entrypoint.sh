#!/bin/bash
set -e

# Start the FastAPI application in the background
uvicorn app.main:app --host 0.0.0.0 --port 8080 &

# Keep container running
sleep infinity
