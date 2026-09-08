"""Compatibility execution endpoints for the zero-budget Growth Agent."""
import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.growth_runs import GrowthAgentRun
from app.services.growth_worker_v3 import run_once
from app.utils.deps import require_admin

router = APIRouter(prefix="/growth-exec", tags=["growth-agent-execution"])

@router.post("/run")
async def run_growth_job(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return await asyncio.to_thread(run_once, True)

@router.get("/status")
async def growth_job_status(db: Session = Depends(get_db), admin=Depends(require_admin)):
    run = db.query(GrowthAgentRun).order_by(GrowthAgentRun.started_at.desc()).first()
    return {"ok": True, "run": None if not run else {"run_key": run.run_key, "status": run.status, "mode": run.mode, "started_at": run.started_at.isoformat(), "finished_at": run.finished_at.isoformat() if run.finished_at else None, "priority": run.priority, "diagnosis": run.diagnosis, "error": run.error}}
