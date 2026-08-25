#!/usr/bin/env bash
set -euo pipefail

# Render Free does not provide a separate pre-deploy command, so run the
# database migration immediately before starting FastAPI.
#
# Do NOT use `python -m alembic`: Alembic is a package without an
# `alembic.__main__` module in this installation. Calling Alembic through
# its Python API works reliably even when the console script is not on PATH.
echo "[BeatHub] Running Alembic migrations..."
python - <<'PY'
from alembic import command
from alembic.config import Config

config = Config("alembic.ini")
command.upgrade(config, "head")
PY

echo "[BeatHub] Alembic migrations complete. Starting FastAPI..."
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
