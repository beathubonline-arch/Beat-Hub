from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.routers.dashboard import _dashboard_context
from app.utils.deps import require_creator

router = APIRouter(tags=["dashboard-analytics"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard/analytics")
def dashboard_analytics(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    context = _dashboard_context(request, db, user, page=1, search="")

    gross = Decimal(str(context.get("gross_revenue") or 0))
    commission = Decimal(str(context.get("platform_commission") or 0))
    net = Decimal(str(context.get("net_earnings") or 0))
    sales = int(context.get("total_sales") or 0)

    context.update(
        gross_revenue=gross,
        platform_commission=commission,
        net_earnings=net,
        total_sales=sales,
        current_year=datetime.utcnow().year,
    )

    return templates.TemplateResponse(
        request,
        "dashboard_analytics.html",
        context,
    )
