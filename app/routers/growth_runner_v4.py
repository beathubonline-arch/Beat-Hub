import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.growth import GrowthProspect, GrowthTouch
from app.models.growth_runs import GrowthAgentRun
from app.services.growth_acquisition_v6 import build_daily_acquisition_queue
from app.services.growth_worker_v4 import run_once
from app.utils.deps import require_admin

router = APIRouter(prefix="/admin/growth-runner", tags=["growth-agent-runner"])
templates = Jinja2Templates(directory="app/templates")

TRACKABLE_STAGES = {
    "outreach_ready", "contacted", "replied", "visited", "registered", "played", "connected", "purchased"
}


def _queue_rows(db: Session, include_progressed: bool = False) -> list[dict]:
    statuses = list(TRACKABLE_STAGES) if include_progressed else ["outreach_ready"]
    prospects = (
        db.query(GrowthProspect)
        .filter(GrowthProspect.status.in_(statuses))
        .order_by(GrowthProspect.fit_score.desc(), GrowthProspect.updated_at.desc())
        .limit(20)
        .all()
    )
    rows = []
    for prospect in prospects:
        draft = (
            db.query(GrowthTouch)
            .filter(GrowthTouch.prospect_id == prospect.id, GrowthTouch.stage == "outreach_ready")
            .order_by(GrowthTouch.created_at.desc())
            .first()
        )
        rows.append({
            "id": prospect.id,
            "prospect_id": prospect.id,
            "name": prospect.name,
            "platform": prospect.platform,
            "public_url": prospect.public_url,
            "location": prospect.location,
            "fit_score": prospect.fit_score or 0,
            "why_fit": prospect.why_fit,
            "recommended_angle": prospect.recommended_angle,
            "matched_track_id": prospect.matched_track_id,
            "matched_track_title": prospect.matched_track.title if prospect.matched_track else None,
            "message": draft.note if draft else None,
            "draft": draft.note if draft else None,
            "status": prospect.status,
        })
    return rows


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
            "mode": acquisition.get("mode"),
            "discovered": acquisition.get("discovered", 0),
            "qualified_queue": acquisition.get("qualified_queue", 0),
            "human_approval_required": acquisition.get("human_approval_required", True),
        },
        "acquisition_queue": acquisition.get("queue", []),
    }


@router.get("/queue")
async def outreach_queue(db: Session = Depends(get_db), admin=Depends(require_admin)):
    rows = _queue_rows(db)
    return {"ok": True, "count": len(rows), "queue": rows, "human_approval_required": True}


@router.get("/queue-ui", response_class=HTMLResponse)
async def outreach_queue_ui(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    rows = _queue_rows(db)
    return templates.TemplateResponse(
        request,
        "growth_queue.html",
        {"request": request, "current_user": admin, "queue": rows},
    )


@router.get("/acquisition-v6", response_class=HTMLResponse)
async def acquisition_v6_page(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    return templates.TemplateResponse(
        request,
        "growth_acquisition_v6.html",
        {"request": request, "current_user": admin, "queue": _queue_rows(db, include_progressed=True)},
    )


@router.get("/acquisition-v6/queue")
async def acquisition_v6_queue(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return JSONResponse({"ok": True, "queue": _queue_rows(db, include_progressed=True)})


@router.post("/acquisition-v6/run")
async def acquisition_v6_run(
    location: str = Query("Kenya", min_length=2, max_length=100),
    limit: int = Query(8, ge=1, le=12),
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    result = await build_daily_acquisition_queue(db, location=location, limit=limit)
    result["queue_snapshot"] = _queue_rows(db, include_progressed=True)
    return JSONResponse(result)


@router.post("/acquisition-v6/prospects/{prospect_id}/contacted")
async def acquisition_v6_contacted(prospect_id: str, request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    prospect = db.query(GrowthProspect).filter(GrowthProspect.id == prospect_id).first()
    if not prospect:
        return JSONResponse({"ok": False, "error": "Prospect not found."}, status_code=404)
    body = await request.json()
    if body.get("approved_by_human") is not True:
        return JSONResponse({"ok": False, "error": "Human approval is required before recording contact."}, status_code=409)
    channel = str(body.get("channel") or prospect.platform or "manual")[:50]
    note = str(body.get("note") or "Human approved and manually sent the prepared outreach.")[:2000]
    prospect.status = "contacted"
    db.add(GrowthTouch(
        prospect_id=prospect.id,
        stage="contacted",
        channel=channel,
        note=note,
        approved_by_human=True,
    ))
    db.commit()
    return JSONResponse({"ok": True, "prospect_id": prospect.id, "status": prospect.status})


@router.post("/acquisition-v6/prospects/{prospect_id}/stage")
async def acquisition_v6_stage(prospect_id: str, request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    prospect = db.query(GrowthProspect).filter(GrowthProspect.id == prospect_id).first()
    if not prospect:
        return JSONResponse({"ok": False, "error": "Prospect not found."}, status_code=404)
    body = await request.json()
    stage = str(body.get("stage") or "").strip().lower()
    if stage not in TRACKABLE_STAGES - {"contacted"}:
        return JSONResponse({"ok": False, "error": "Unsupported stage."}, status_code=400)
    prospect.status = stage
    db.add(GrowthTouch(
        prospect_id=prospect.id,
        stage=stage,
        channel=str(body.get("channel") or prospect.platform or "manual")[:50],
        note=str(body.get("note") or f"Moved to {stage} from Growth Agent V6.")[:2000],
        approved_by_human=False,
    ))
    db.commit()
    return JSONResponse({"ok": True, "prospect_id": prospect.id, "status": prospect.status})
