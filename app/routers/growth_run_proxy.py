"""Compatibility endpoint for the visible Growth OS Run Agent control.

The dashboard historically posts to /admin/growth/run. Keep that URL stable,
but execute the same persisted zero-budget worker used by the scheduler and
admin runner so manual runs are auditable in growth_agent_runs.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.growth_agent import campaign_snapshot, funnel_snapshot
from app.services.growth_worker_v4 import run_once
from app.utils.deps import require_admin

router = APIRouter(prefix="/growth", tags=["growth-agent"])


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
