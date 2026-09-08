from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.growth_agent import GrowthAgentError, build_snapshot, run_growth_agent, scout_prospects
from app.utils.deps import require_admin

# This router is mounted inside the existing /admin router in app/routers/__init__.py.
router = APIRouter(prefix="/growth", tags=["growth-agent"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def growth_dashboard(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    snapshot = build_snapshot(db)
    return templates.TemplateResponse(
        request,
        "growth_agent.html",
        {"request": request, "current_user": admin, "snapshot": snapshot, "plan": None, "error": None},
    )


@router.post("/run")
async def run_growth(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    snapshot = build_snapshot(db)
    try:
        plan = await run_growth_agent(snapshot)
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc), "snapshot": snapshot}, status_code=503)
    return JSONResponse({"ok": True, "snapshot": snapshot, "plan": plan})


@router.get("/scout")
async def scout(
    request: Request,
    q: str = Query(..., min_length=3, max_length=300),
    location: str = Query("Kenya", min_length=2, max_length=100),
    limit: int = Query(10, ge=1, le=20),
    admin=Depends(require_admin),
):
    try:
        result = await scout_prospects(q, location=location, limit=limit)
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, **result})
