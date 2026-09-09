from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.database import SessionLocal
from app.models.growth_runs import GrowthAgentRun
from app.services.growth_acquisition_v6 import run_acquisition_queue
from app.services.growth_agent import build_snapshot, run_growth_agent
from app.services.growth_payment_recovery import reconcile_recent_payments
from app.services.growth_verified_bootstrap import run_verified_bootstrap_queue

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


def _recover_session(db) -> None:
    """Discard a broken/idle DB connection so SQLAlchemy reconnects on next use."""
    try:
        db.rollback()
    except Exception:
        pass
    try:
        db.close()
    except Exception:
        pass


def _build_acquisition(db) -> dict:
    try:
        acquisition = run_acquisition_queue(db, location="Kenya", limit=8)
    except (OperationalError, DBAPIError) as exc:
        logger.warning(
            "[BeatHub Growth Agent] live acquisition lost DB connection (%s); reconnecting and using verified public bootstrap",
            type(exc).__name__,
        )
        _recover_session(db)
        return run_verified_bootstrap_queue(db, limit=8)
    except Exception as exc:
        logger.warning(
            "[BeatHub Growth Agent] live acquisition failed (%s); using verified public bootstrap",
            type(exc).__name__,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return run_verified_bootstrap_queue(db, limit=8)

    if int(acquisition.get("qualified_queue", 0) or 0) == 0:
        logger.warning(
            "[BeatHub Growth Agent] live acquisition returned no qualified prospects; using verified public bootstrap"
        )
        return run_verified_bootstrap_queue(db, limit=8)
    return acquisition


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
            acquisition = _build_acquisition(db)
            # _build_acquisition may recycle the session after a transient disconnect,
            # so reload the row before persisting the refreshed queue.
            existing = db.query(GrowthAgentRun).filter(GrowthAgentRun.run_key == key).first()
            if existing:
                _merge_existing_plan(existing, acquisition)
                db.commit()
            logger.info(
                "[BeatHub Growth Agent] refreshed acquisition queue: status=%s mode=%s qualified=%s",
                acquisition.get("status"),
                acquisition.get("mode"),
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

        payment_recovery = asyncio.run(reconcile_recent_payments(db, days=14, limit=20))
        snapshot = build_snapshot(db)
        plan = asyncio.run(run_growth_agent(snapshot))
        plan["payment_recovery"] = payment_recovery

        acquisition = _build_acquisition(db)
        plan["acquisition_queue"] = acquisition

        # The acquisition path can intentionally recycle the session after an idle
        # SSL reset. Always reload today's run before writing final state.
        run = db.query(GrowthAgentRun).filter(GrowthAgentRun.run_key == key).first()
        if not run:
            raise RuntimeError("GrowthAgentRun disappeared during acquisition cycle.")
        run.finished_at = datetime.now(timezone.utc)
        run.status = "completed"
        run.priority = str(plan.get("priority") or "")
        run.diagnosis = str(plan.get("diagnosis") or "")
        run.plan_json = json.dumps(plan, default=str)
        db.commit()
        logger.info(
            "[BeatHub Growth Agent] completed daily cycle %s; acquisition status=%s mode=%s qualified=%s",
            key,
            acquisition.get("status"),
            acquisition.get("mode"),
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
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("[BeatHub Growth Agent] failed")
        return {"ok": False, "status": "failed", "error": str(exc)[:4000]}
    finally:
        if locked:
            try:
                db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
        db.close()
