from pathlib import Path
import subprocess
import sys

# NOTE: This file is updated through the existing BeatHub main.py. The startup
# migration block below is intentionally defensive and verbose so Render logs
# expose the actual Alembic error instead of only the generic RuntimeError.
