import logging
import os
import time
from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation
from pathlib import Path
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.database import Base, engine
from app.models import *  # noqa: F401,F403
from app.middleware.security import SameOriginMiddleware
from app.routers import (
    admin,
    admin_mpesa_payout,
    admin_unified_sales,
    api_v1,
    api_downloads,
    audio_preview,
    auth,
    checkout,
    creator_store,
    dashboard,
    dashboard_analytics,
    merchandise,
    merchandise_account,
    music,
    music_publish,
    notifications,
    pages,
    paystack_checkout,
    payout_admin,
    track_catalog,
)
from app.services.payout_policy import PAYOUT_MINIMUM

logger = logging.getLogger("beathub")
BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title=getattr(settings, "APP_NAME", "BeatHub"), description="BeatHub — beats, music, sessions, producer stores and creator merchandise.", version="1.0.0")
app.state.beathub_production = bool(settings.is_production)
app.state.beathub_base_url = str(getattr(settings, "BASE_URL", "") or "").strip().rstrip("/")


def _session_secret() -> str:
    value = os.getenv("SESSION_SECRET") or getattr(settings, "SESSION_SECRET", None)
    if value and str(value).strip(): return str(value).strip()
    logger.warning("SESSION_SECRET is not configured. Set SESSION_SECRET in production.")
    return "beathub-development-session-secret-change-me"


def _session_max_age() -> int:
    raw = os.getenv("SESSION_MAX_AGE") or getattr(settings, "SESSION_MAX_AGE", None) or 60 * 60 * 24 * 30
    try: value = int(raw)
    except (TypeError, ValueError): value = 60 * 60 * 24 * 30
    return max(300, min(value, 60 * 60 * 24 * 365))


def _session_https_only() -> bool:
    raw = os.getenv("SESSION_HTTPS_ONLY") or getattr(settings, "SESSION_HTTPS_ONLY", None)
    if raw is None: return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class HomepageMotionMiddleware:
    LINK = b'<link rel="stylesheet" href="/static/css/home-animation.css?v=20260825" data-beathub-home-motion="1">'
    INLINE = b'<style data-beathub-home-motion-inline="1">@keyframes beathub-vinyl-spin-inline{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}.vinyl{animation:beathub-vinyl-spin-inline 12s linear infinite !important;transform-origin:center center !important;will-change:transform}</style>'
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send); return
        body_parts=[]
        async def send_wrapper(message):
            if message["type"] == "http.response.body" and message.get("body"):
                body_parts.append(message["body"])
                if not message.get("more_body", False):
                    body=b"".join(body_parts)
                    headers=dict(message.get("headers", []))
                    content_type=dict(headers).get(b"content-type", b"").lower()
                    if b"text/html" in content_type:
                        body=body.replace(b"</head>", self.LINK+self.INLINE+b"</head>")
                    message=dict(message); message["body"]=body; message.setdefault("headers",[])
                    message["headers"]=[(k,v) for k,v in message["headers"] if k.lower() not in {b"content-length",b"content-encoding"}]
                    message["headers"].append((b"content-length",str(len(body)).encode()))
            await send(message)
        await self.app(scope, receive, send_wrapper)


app.add_middleware(HomepageMotionMiddleware)
app.add_middleware(
    BaseHTTPMiddleware,
    dispatch=lambda request, call_next: call_next(request),
)
app.add_middleware(SameOriginMiddleware)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
MEDIA_DIR = Path(getattr(settings, "MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

app.include_router(api_v1.router)
app.include_router(api_downloads.router)
app.include_router(auth.router)
app.include_router(checkout.router)
app.include_router(creator_store.router)
app.include_router(dashboard.router)
app.include_router(dashboard_analytics.router)
app.include_router(merchandise.router)
app.include_router(merchandise_account.router)
app.include_router(music.router)
app.include_router(music_publish.router)
app.include_router(notifications.router)
app.include_router(pages.router)
app.include_router(paystack_checkout.router)
app.include_router(payout_admin.router)
app.include_router(track_catalog.router)
app.include_router(admin.router)
app.include_router(admin_mpesa_payout.router)
app.include_router(admin_unified_sales.router)
app.include_router(audio_preview.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
