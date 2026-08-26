#!/usr/bin/env bash
set -euo pipefail

# Render Free does not expose a separate pre-deploy command. Start the web
# process immediately so Render can detect $PORT, while migrations run once
# in the same deploy before the process is allowed to exit.
#
# The migration runs in the background after Uvicorn binds the port. If the
# migration fails, the web process is terminated and the deploy fails instead
# of silently serving an incomplete schema.

MIGRATION_STATUS=0

echo "[BeatHub] Starting FastAPI on ${PORT:-10000}..."
python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}" &
WEB_PID=$!

cleanup() {
    kill "$WEB_PID" 2>/dev/null || true
    wait "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Give Uvicorn a moment to bind before the migration starts. This avoids
# Render's port detector waiting behind database work.
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

# Keep Uvicorn as the foreground process after migrations finish.
wait "$WEB_PID"
