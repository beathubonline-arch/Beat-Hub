#!/usr/bin/env bash
set -euo pipefail

# Run the canonical Alembic migrations before starting FastAPI.
# This is intentionally outside Python import/startup code so Render gets a
# clear migration failure instead of a hanging web process with no open port.
alembic upgrade head

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
