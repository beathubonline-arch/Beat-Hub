from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.database import SessionLocal
from app.models.growth_runs import GrowthAgentRun
from app.services.growth_agent import build_snapshot, run_growth_agent

logger = logging.getLogger("beathub.growth_worker")
LOCK_KEY = 734820261


def run_once(force: bool = False) -> dict:
    db = SessionLocal()
    locked = False
    run_key = datetime.now(timezone.utc).date().isoformat()
    try:
        try:
            locked = bool(db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).scalar())
        except Exception:
            locked = True
        if not locked:
            return {"ok": True, "status": "already_running"}
        if not force and db.query(GrowthAgentRun).filter(GrowthAgentRun.run_key == run_key, GrowthAgentRun.status == "completed").first():
            return {"ok": True, "status": "already_completed", "run_key": run_key}
        started = datetime.now(timezone.utc)
        run = GrowthAgentRun(id=hashlib.sha256(f"{run_key}:{started.timestamp()}".encode()).hexdigest()[:32], run_key=run_key, started_at=started, status="running", mode="local")
        db.add(run)
        db.commit()
        snapshot = build_snapshot(db)
        plan = asyncio.run(run_growth_agent(snapshot))
        run.finished_at = datetime.now(timezone.utc)
        run.status = "completed"
        run.priority = str(plan.get("priority") or "")
        run.diagnosis = str(plan.get("diagnosis") or "")
        run.plan_json = json.dumps(plan, default=str)
        db.commit()
        logger.info("Growth Agent cycle completed: priority=%s", run.priority)
        return {"ok": True, "status": "completed", "run_key": run_key, "snapshot": snapshot, "plan": plan, "ran_at": run.finished_at.isoformat()}
    except Exception as exc:
        db.rollback()
        logger.exception("Growth Agent cycle failed")
        return {"ok": False, "status": "failed", "error": str(exc)[:4000]}
    finally:
        if locked:
            try:
                db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                db.commit()
            except Exception:
                db.rollback()
        db.close()
