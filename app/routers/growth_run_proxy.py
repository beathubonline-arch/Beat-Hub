"""Compatibility and dashboard endpoint for the Growth OS.

The visible admin URL remains /admin/growth. Manual runs use the same persisted
zero-budget worker as the scheduler, while this router renders the V5 dashboard.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.growth_agent import campaign_snapshot, ensure_campaign, funnel_snapshot, prospect_snapshot
from app.services.growth_agent import build_snapshot
from app.services.growth_worker_v4 import run_once
from app.utils.deps import require_admin

router = APIRouter(prefix="/growth", tags=["growth-agent"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def growth_dashboard_v5(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    snapshot = build_snapshot(db)
    campaign = ensure_campaign(db)
    return templates.TemplateResponse(
        request,
        "growth_os_v5.html",
        {
            "request": request,
            "current_user": admin,
            "snapshot": snapshot,
            "funnel": funnel_snapshot(db),
            "campaign": campaign,
            "prospects": prospect_snapshot(db),
        },
    )


@router.post("/run")
async def run_growth_via_worker(
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    result = await asyncio.to_thread(run_once, True)
    if not result.get("ok"):
        return JSONResponse(result, status_code=500)
    return JSONResponse({
        "ok": True,
        "status": result.get("status"),
        "run_key": result.get("run_key"),
        "ran_at": result.get("ran_at"),
        "snapshot": result.get("snapshot"),
        "funnel": funnel_snapshot(db),
        "campaign": campaign_snapshot(db),
        "plan": result.get("plan"),
    })
