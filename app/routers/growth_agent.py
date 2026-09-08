from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Track
from app.services.growth_agent import (
    GrowthAgentError,
    build_snapshot,
    generate_content,
    generate_outreach,
    match_beats,
    run_growth_agent,
    scout_prospects,
)
from app.utils.deps import require_admin

router = APIRouter(prefix="/growth", tags=["growth-agent"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def growth_dashboard(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    snapshot = build_snapshot(db)
    return templates.TemplateResponse(request, "growth_agent.html", {"request": request, "current_user": admin, "snapshot": snapshot, "plan": None, "error": None})


@router.post("/run")
async def run_growth(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    snapshot = build_snapshot(db)
    try:
        plan = await run_growth_agent(snapshot)
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc), "snapshot": snapshot}, status_code=503)
    return JSONResponse({"ok": True, "snapshot": snapshot, "plan": plan})


@router.get("/scout")
async def scout(q: str = Query(..., min_length=3, max_length=300), location: str = Query("Kenya", min_length=2, max_length=100), limit: int = Query(10, ge=1, le=20), admin=Depends(require_admin)):
    try:
        result = await scout_prospects(q, location=location, limit=limit)
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, **result})


@router.post("/match")
async def match(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    body = await request.json()
    request_text = str(body.get("artist_request", "")).strip()
    if len(request_text) < 3:
        return JSONResponse({"ok": False, "error": "artist_request must be at least 3 characters."}, status_code=400)
    tracks = db.query(Track).filter(Track.is_published.is_(True)).order_by(Track.created_at.desc()).limit(50).all()
    catalog = [{"id": str(t.id), "title": t.title, "genre": t.genre, "bpm": t.bpm, "currency": t.currency, "price": float(t.price or 0)} for t in tracks]
    try:
        result = await match_beats(request_text, catalog)
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, **result})


@router.post("/outreach")
async def outreach(request: Request, admin=Depends(require_admin)):
    body = await request.json()
    try:
        result = await generate_outreach(body.get("prospect", {}), body.get("matches", []))
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, **result})


@router.post("/content")
async def content(request: Request, admin=Depends(require_admin)):
    body = await request.json()
    try:
        result = await generate_content(body.get("track", body))
    except GrowthAgentError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, **result})
