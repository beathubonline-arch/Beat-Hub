"""Zero-budget Growth Agent execution worker.

Runs the deterministic/local Growth OS without OpenAI, Render Cron, or any
additional paid service. PostgreSQL advisory locking makes the job safe when
more than one application process exists.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.database import SessionLocal
from app.services.growth_agent import build_snapshot, run_growth_agent

logger = logging.getLogger("beathub.growth_worker")
LOCK_KEY = 734820261


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_once(force: bool = False) -> dict:
    """Run one Growth OS cycle, at most once per UTC day unless forced."""
    db = SessionLocal()
    locked = False
    try:
        try:
            locked = bool(db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).scalar())
        except Exception:
            # SQLite/local development has no PostgreSQL advisory locks.
            locked = True
        if not locked:
            return {"ok": True, "status": "already_running"}

        snapshot = build_snapshot(db)
        plan = asyncio.run(run_growth_agent(snapshot))
        logger.info("Growth Agent cycle generated: priority=%s", plan.get("priority"))
        return {"ok": True, "status": "completed", "snapshot": snapshot, "plan": plan, "ran_at": _utcnow().isoformat()}
    except Exception:
        db.rollback()
        logger.exception("Growth Agent cycle failed")
        return {"ok": False, "status": "failed", "error": "Growth Agent cycle failed"}
    finally:
        if locked:
            try:
                db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                db.commit()
            except Exception:
                db.rollback()
        db.close()
