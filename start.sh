#!/usr/bin/env bash
set -euo pipefail

# BeatHub deployment marker: creator marketplace dashboard V4.
echo "[BeatHub] Deployment: Creator Marketplace Dashboard V4"

MIGRATION_STATUS=0

echo "[BeatHub] Starting FastAPI on ${PORT:-10000}..."
python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}" &
WEB_PID=$!

cleanup() {
    kill "$WEB_PID" 2>/dev/null || true
    wait "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 2

echo "[BeatHub] Running Alembic migrations..."
if python - <<'PY'
from alembic import command
from alembic.config import Config

config = Config("alembic.ini")
command.upgrade(config, "head")
PY
then
    echo "[BeatHub] Alembic migrations complete."
else
    MIGRATION_STATUS=$?
    echo "[BeatHub] Alembic migration failed with status ${MIGRATION_STATUS}. Stopping web process."
fi

if [ "$MIGRATION_STATUS" -ne 0 ]; then
    exit "$MIGRATION_STATUS"
fi

wait "$WEB_PID"
