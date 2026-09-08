from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.growth_runs import GrowthAgentRun
from app.services.growth_worker_v2 import run_once
from app.utils.deps import require_admin

router = APIRouter(prefix="/admin/growth", tags=["growth-agent-runner"])


@router.post("/run-now")
async def run_now(db: Session = Depends(get_db), admin=Depends(require_admin)):
    result = await __import__("asyncio").to_thread(run_once, True)
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)


@router.get("/run-status")
async def run_status(db: Session = Depends(get_db), admin=Depends(require_admin)):
    run = db.query(GrowthAgentRun).order_by(GrowthAgentRun.started_at.desc()).first()
    if not run:
        return {"ok": True, "run": None}
    return {"ok": True, "run": {"id": run.id, "run_key": run.run_key, "started_at": run.started_at.isoformat(), "finished_at": run.finished_at.isoformat() if run.finished_at else None, "status": run.status, "mode": run.mode, "priority": run.priority, "diagnosis": run.diagnosis, "error": run.error}}
