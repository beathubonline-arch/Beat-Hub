#!/usr/bin/env bash
set -euo pipefail

# Render Free does not provide a pre-deploy command, so migrations run here
# before FastAPI starts. Use Python's module runner instead of relying on the
# alembic executable being present on PATH.
echo "[BeatHub] Running Alembic migrations..."
python -m alembic upgrade head
echo "[BeatHub] Alembic migrations complete. Starting FastAPI..."

exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
