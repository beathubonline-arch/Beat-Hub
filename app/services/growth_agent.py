from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.music import Track
from app.models.order import Order, OrderStatus
from app.models.profile import Profile
from app.models.user import User, UserRole


class GrowthAgentError(RuntimeError):
    pass


def _money(value):
    try:
        return float(Decimal(str(value or 0)))
    except Exception:
        return 0.0


def build_snapshot(db: Session) -> dict:
    """Build a small, privacy-safe growth snapshot from BeatHub's live DB."""
    since = datetime.utcnow() - timedelta(days=7)
    users = db.query(func.count(User.id)).scalar() or 0
    creators = db.query(func.count(User.id)).filter(User.role == UserRole.CREATOR).scalar() or 0
    tracks = db.query(func.count(Track.id)).filter(Track.is_published.is_(True)).scalar() or 0
    profiles = db.query(func.count(Profile.id)).scalar() or 0
    completed = db.query(func.count(Order.id)).filter(Order.status == OrderStatus.COMPLETED).scalar() or 0
    recent_users = db.query(func.count(User.id)).filter(User.created_at >= since).scalar() or 0
    recent_orders = db.query(func.count(Order.id)).filter(Order.created_at >= since).scalar() or 0
    recent_completed = db.query(func.count(Order.id)).filter(
        Order.created_at >= since, Order.status == OrderStatus.COMPLETED
    ).scalar() or 0
    recent_gmv = db.query(func.coalesce(func.sum(Order.gross_amount), 0)).filter(
        Order.created_at >= since, Order.status == OrderStatus.COMPLETED
    ).scalar() or 0

    latest_tracks = db.query(Track).filter(Track.is_published.is_(True)).order_by(Track.created_at.desc()).limit(20).all()
    track_items = [
        {
            "title": t.title,
            "genre": t.genre,
            "bpm": t.bpm,
            "currency": t.currency,
            "price": _money(t.price),
            "sales_model": getattr(t.sales_model, "value", t.sales_model),
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in latest_tracks
    ]

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "window": "last_7_days",
        "base_url": settings.BASE_URL,
        "totals": {
            "users": users,
            "creators": creators,
            "published_tracks": tracks,
            "profiles": profiles,
            "completed_orders_all_time": completed,
        },
        "last_7_days": {
            "new_users": recent_users,
            "orders": recent_orders,
            "completed_orders": recent_completed,
            "completed_gmv": _money(recent_gmv),
        },
        "latest_tracks": track_items,
    }


async def run_growth_agent(snapshot: dict) -> dict:
    api_key = str(getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    model = str(getattr(settings, "OPENAI_MODEL", "") or "").strip()
    if not api_key:
        raise GrowthAgentError("OPENAI_API_KEY is not configured.")
    if not model:
        raise GrowthAgentError("OPENAI_MODEL is not configured.")

    instructions = """You are BeatHub Growth Agent, a ruthless zero-budget growth operator for an early music marketplace.
BeatHub connects artists and buyers with independent producers, beats, sessions and creator stores.
Your job is to turn the supplied first-party product snapshot into an actionable 24-hour plan.
Do not invent metrics, users, sales or capabilities. Do not recommend buying ads or followers.
Prefer organic distribution, creator-to-creator loops, personalized outreach, UGC, short-form experiments,
community participation and measurable referral loops. Do not recommend spam, scraping private data,
automated unsolicited DMs, credential sharing, or platform-rule evasion.
Return valid JSON with keys: diagnosis, priority, daily_targets, prospects_to_seek, content_experiments,
outreach_angles, product_loops, metrics_to_watch, kill_list, tomorrow_test.
Keep the plan concrete, ranked and small enough for one founder to execute in one day."""

    payload = {
        "model": model,
        "instructions": instructions,
        "input": json.dumps(snapshot, ensure_ascii=False),
        "store": False,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.status_code >= 400:
        raise GrowthAgentError(f"OpenAI request failed ({response.status_code}).")

    data = response.json()
    text = data.get("output_text")
    if not text:
        chunks = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    chunks.append(content.get("text", ""))
        text = "\n".join(chunks).strip()
    if not text:
        raise GrowthAgentError("Growth agent returned no usable output.")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"diagnosis": text, "raw": True}
