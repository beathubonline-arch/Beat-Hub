#!/usr/bin/env bash
set -euo pipefail

# BeatHub deployment marker: creator marketplace dashboard V6.
echo "[BeatHub] Deployment: Creator Marketplace Dashboard V6"

MIGRATION_STATUS=0

echo "[BeatHub] Starting FastAPI on ${PORT:-10000}..."
python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}" &
WEB_PID=$!

trap 'kill "$WEB_PID" 2>/dev/null || true' EXIT

echo "[BeatHub] Running Alembic migrations..."
if alembic upgrade head; then
  echo "[BeatHub] Alembic migrations complete."
else
  MIGRATION_STATUS=$?
  echo "[BeatHub] Alembic migration failed with status ${MIGRATION_STATUS}."
  kill "$WEB_PID" 2>/dev/null || true
  wait "$WEB_PID" 2>/dev/null || true
  exit "$MIGRATION_STATUS"
fi

wait "$WEB_PID"
