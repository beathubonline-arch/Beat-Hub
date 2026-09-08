from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.growth_agent import GrowthAgentError, build_snapshot, run_growth_agent
from app.utils.deps import require_admin

# This router is mounted inside the existing /admin router in app/routers/__init__.py.
router = APIRouter(prefix="/growth", tags=["growth-agent"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def growth_dashboard(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    snapshot = build_snapshot(db)
    return templates.TemplateResponse(
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
