import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.growth_runs import GrowthAgentRun
from app.services.growth_worker_v2 import run_once
from app.utils.deps import require_admin

router = APIRouter(prefix="/admin/growth-runner", tags=["growth-agent-runner"])


@router.post("/run-now")
async def run_now(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return await asyncio.to_thread(run_once, True)


@router.get("/status")
async def run_status(db: Session = Depends(get_db), admin=Depends(require_admin)):
    run = db.query(GrowthAgentRun).order_by(GrowthAgentRun.started_at.desc()).first()
    if not run:
        return {"ok": True, "run": None}
    return {"ok": True, "run": {"run_key": run.run_key, "status": run.status, "mode": run.mode, "started_at": run.started_at.isoformat(), "finished_at": run.finished_at.isoformat() if run.finished_at else None, "priority": run.priority, "diagnosis": run.diagnosis, "error": run.error}}
