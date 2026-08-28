#!/usr/bin/env bash
set -euo pipefail

# BeatHub deployment marker: marketplace content classification V7.0.
echo "[BeatHub] Deployment: Marketplace Content Classification V7.0"

echo "[BeatHub] Running Alembic migrations before starting FastAPI..."
alembic upgrade head
echo "[BeatHub] Alembic migrations complete."

echo "[BeatHub] Starting FastAPI on ${PORT:-10000}..."

exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
