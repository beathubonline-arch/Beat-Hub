import asyncio
import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.growth import GrowthProspect, GrowthTouch
from app.models.growth_runs import GrowthAgentRun
from app.services.growth_worker_v4 import run_once
from app.utils.deps import require_admin

router = APIRouter(prefix="/admin/growth-runner", tags=["growth-agent-runner"])


@router.post("/run-now")
async def run_now(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return await asyncio.to_thread(run_once, True)


@router.get("/status")
async def run_status(db: Session = Depends(get_db), admin=Depends(require_admin)):
    run = db.query(GrowthAgentRun).order_by(GrowthAgentRun.started_at.desc()).first()
    if not run:
        return {"ok": True, "run": None, "acquisition_queue": []}
    try:
        plan = json.loads(run.plan_json or "{}")
    except Exception:
        plan = {}
    acquisition = plan.get("acquisition_queue") or {}
    return {
        "ok": True,
        "run": {
            "run_key": run.run_key,
            "status": run.status,
            "mode": run.mode,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "priority": run.priority,
            "diagnosis": run.diagnosis,
            "error": run.error,
        },
        "acquisition": {
            "status": acquisition.get("status"),
            "discovered": acquisition.get("discovered", 0),
            "qualified_queue": acquisition.get("qualified_queue", 0),
            "human_approval_required": acquisition.get("human_approval_required", True),
        },
        "acquisition_queue": acquisition.get("queue", []),
    }


@router.get("/queue")
async def outreach_queue(db: Session = Depends(get_db), admin=Depends(require_admin)):
    prospects = (
        db.query(GrowthProspect)
        .filter(GrowthProspect.status == "outreach_ready")
        .order_by(GrowthProspect.fit_score.desc(), GrowthProspect.updated_at.desc())
        .limit(20)
        .all()
    )
    rows = []
    for prospect in prospects:
        draft = (
            db.query(GrowthTouch)
            .filter(
                GrowthTouch.prospect_id == prospect.id,
                GrowthTouch.stage == "outreach_ready",
            )
            .order_by(GrowthTouch.created_at.desc())
            .first()
        )
        rows.append({
            "prospect_id": prospect.id,
            "name": prospect.name,
            "platform": prospect.platform,
            "public_url": prospect.public_url,
            "fit_score": prospect.fit_score,
            "why_fit": prospect.why_fit,
            "matched_track_id": prospect.matched_track_id,
            "matched_track_title": prospect.matched_track.title if prospect.matched_track else None,
            "message": draft.note if draft else None,
            "status": prospect.status,
        })
    return {"ok": True, "count": len(rows), "queue": rows, "human_approval_required": True}
