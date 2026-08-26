#!/usr/bin/env bash
set -euo pipefail

# Render Web Service start command: start FastAPI immediately.
# Database migrations must run before this process (Render pre-deploy/build
# phase), not while Render is waiting for the web process to bind its port.
echo "[BeatHub] Starting FastAPI..."
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
