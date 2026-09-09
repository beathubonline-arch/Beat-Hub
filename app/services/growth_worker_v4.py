from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.database import SessionLocal
from app.models.growth_runs import GrowthAgentRun
from app.services.growth_acquisition_v6 import run_acquisition_queue
from app.services.growth_agent import build_snapshot, run_growth_agent
from app.services.growth_payment_recovery import reconcile_recent_payments

logger = logging.getLogger("beathub.growth_worker")
LOCK_KEY = 734820261


def _merge_existing_plan(run: GrowthAgentRun, acquisition: dict) -> None:
    try:
        plan = json.loads(run.plan_json or "{}")
    except Exception:
        plan = {}
    plan["acquisition_queue"] = acquisition
    run.plan_json = json.dumps(plan, default=str)
    run.finished_at = datetime.now(timezone.utc)


def run_once(force: bool = False) -> dict:
    db = SessionLocal()
    locked = False
    key = datetime.now(timezone.utc).date().isoformat()
    try:
        try:
            locked = bool(db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).scalar())
        except Exception:
            locked = True
        if not locked:
            return {"ok": True, "status": "already_running"}

        existing = db.query(GrowthAgentRun).filter(GrowthAgentRun.run_key == key).first()
        if existing and existing.status == "completed" and not force:
            acquisition = run_acquisition_queue(db, location="Kenya", limit=8)
            _merge_existing_plan(existing, acquisition)
            db.commit()
            logger.info(
                "[BeatHub Growth Agent] refreshed acquisition queue: status=%s qualified=%s",
                acquisition.get("status"),
                acquisition.get("qualified_queue", 0),
            )
            return {
                "ok": True,
                "status": "acquisition_refreshed",
                "run_key": key,
                "acquisition_queue": acquisition,
            }

        now = datetime.now(timezone.utc)
        if existing:
            run = existing
            run.started_at = now
            run.finished_at = None
            run.status = "running"
            run.error = None
        else:
            run = GrowthAgentRun(
                id=hashlib.sha256(f"{key}:{now.timestamp()}".encode()).hexdigest()[:32],
                run_key=key,
                started_at=now,
                status="running",
                mode="local",
            )
            db.add(run)
        db.commit()

        # Verify existing checkout intent first; never create/retry a charge here.
        payment_recovery = asyncio.run(reconcile_recent_payments(db, days=14, limit=20))
        snapshot = build_snapshot(db)
        plan = asyncio.run(run_growth_agent(snapshot))
        plan["payment_recovery"] = payment_recovery

        # Growth Agent V6: discover public creator profiles, qualify them, match
        # BeatHub catalog items and create a human-approved outreach queue.
        acquisition = run_acquisition_queue(db, location="Kenya", limit=8)
        plan["acquisition_queue"] = acquisition

        run.finished_at = datetime.now(timezone.utc)
        run.status = "completed"
        run.priority = str(plan.get("priority") or "")
        run.diagnosis = str(plan.get("diagnosis") or "")
        run.plan_json = json.dumps(plan, default=str)
        db.commit()
        logger.info(
            "[BeatHub Growth Agent] completed daily cycle %s; acquisition status=%s qualified=%s",
            key,
            acquisition.get("status"),
            acquisition.get("qualified_queue", 0),
        )
        return {
            "ok": True,
            "status": "completed",
            "run_key": key,
            "snapshot": snapshot,
            "plan": plan,
            "ran_at": run.finished_at.isoformat(),
        }
    except Exception as exc:
        db.rollback()
        logger.exception("[BeatHub Growth Agent] failed")
        return {"ok": False, "status": "failed", "error": str(exc)[:4000]}
    finally:
        if locked:
            try:
                db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                db.commit()
            except Exception:
                db.rollback()
        db.close()
