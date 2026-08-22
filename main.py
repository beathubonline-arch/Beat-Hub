import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import Base, engine
from app.routers import (
admin,
auth,
checkout,
dashboard,
mpesa_callback,
music,
pages,
)

try:
from app.routers import merchandise
except ImportError:
merchandise = None

from app.utils.deps import require_admin, require_creator

logger = logging.getLogger("beathub")

BASE_DIR = Path(**file**).resolve().parent
APP_DIR = BASE_DIR / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
title=getattr(settings, "APP_NAME", "BeatHub"),
description=(
"BeatHub — beats, music, sessions, producer stores "
"and creator merchandise."
),
version="1.0.0",
)

def _session_secret() -> str:
value = os.getenv("SESSION_SECRET")

```
if value and value.strip():
    return value.strip()

value = getattr(settings, "SESSION_SECRET", None)

if value and str(value).strip():
    return str(value).strip()

logger.warning(
    "SESSION_SECRET is not configured. "
    "Using a temporary development secret."
)

return "beathub-development-session-secret-change-me"
```

def _session_max_age() -> int:
raw = (
os.getenv("SESSION_MAX_AGE")
or getattr(settings, "SESSION_MAX_AGE", None)
or 60 * 60 * 24 * 30
)

```
try:
    value = int(raw)
except (TypeError, ValueError):
    value = 60 * 60 * 24 * 30

return max(
    300,
    min(
        value,
        60 * 60 * 24 * 365,
    ),
)
```

def _session_https_only() -> bool:
raw = (
os.getenv("SESSION_HTTPS_ONLY")
or getattr(settings, "SESSION_HTTPS_ONLY", None)
)

```
if raw is None:
    return True

return str(raw).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
```

app.add_middleware(
SessionMiddleware,
secret_key=_session_secret(),
session_cookie="beathub_session",
max_age=_session_max_age(),
same_site="lax",
https_only=_session_https_only(),
)

templates = Jinja2Templates(
directory=str(TEMPLATES_DIR)
)

app.mount(
"/static",
StaticFiles(
directory=str(STATIC_DIR),
),
name="static",
)

try:
Base.metadata.create_all(
bind=engine,
)
except Exception:
logger.exception(
"Database table initialization failed."
)
raise

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(mpesa_callback.router)
app.include_router(dashboard.router)
app.include_router(admin.router)

if merchandise is not None:
app.include_router(
merchandise.router,
)

def _template_context(
request: Request,
current_user=None,
**extra,
):
context = {
"request": request,
"current_user": current_user,
"user": current_user,
"current_year": 2026,
}

```
context.update(extra)

return context
```

def _template_exists(
template_name: str,
) -> bool:
return (
TEMPLATES_DIR / template_name
).is_file()

@app.get(
"/artist/dashboard",
include_in_schema=False,
)
@app.get(
"/creator/dashboard",
include_in_schema=False,
)
@app.get(
"/producer/dashboard",
include_in_schema=False,
)
@app.get(
"/dashboard/home",
include_in_schema=False,
)
@app.get(
"/dashboard/index",
include_in_schema=False,
)
def dashboard_alias(
user=Depends(require_creator),
):
return RedirectResponse(
url="/dashboard",
status_code=303,
)

@app.get(
"/creator/withdraw",
include_in_schema=False,
)
@app.get(
"/producer/withdraw",
include_in_schema=False,
)
def creator_withdraw_alias(
user=Depends(require_creator),
):
return RedirectResponse(
url="/dashboard/withdraw",
status_code=303,
)

@app.get(
"/admin/withdrawal",
include_in_schema=False,
)
def admin_withdraw_alias(
user=Depends(require_admin),
):
return RedirectResponse(
url="/admin/withdraw",
status_code=303,
)

@app.api_route(
"/healthz",
methods=["GET", "HEAD"],
)
def healthz():
return {
"status": "ok",
"app": getattr(
settings,
"APP_NAME",
"BeatHub",
),
"env": getattr(
settings,
"APP_ENV",
"production",
),
"storage": getattr(
settings,
"MEDIA_STORAGE",
"local",
),
"r2_enabled": bool(
getattr(
settings,
"r2_enabled",
False,
)
),
"r2_bucket_configured": bool(
getattr(
settings,
"R2_BUCKET_NAME",
None,
)
),
"r2_endpoint_configured": bool(
getattr(
settings,
"r2_endpoint_url",
None,
)
),
}

@app.get(
"/favicon.ico",
include_in_schema=False,
)
def favicon_compatibility():
favicon = STATIC_DIR / "favicon.ico"

```
if favicon.is_file():
    return FileResponse(
        path=str(favicon),
        media_type="image/x-icon",
    )

return Response(
    status_code=204,
)
```

@app.exception_handler(
StarletteHTTPException,
)
async def http_exception_handler(
request: Request,
exc: StarletteHTTPException,
):
if exc.status_code == 401:
return RedirectResponse(
url=(
"/login?"
"error=Please%20log%20in%20to%20continue."
),
status_code=303,
)

```
if exc.status_code == 403:
    template = "errors/403.html"

    if _template_exists(template):
        return templates.TemplateResponse(
            request,
            template,
            _template_context(
                request,
                detail=exc.detail,
            ),
            status_code=403,
        )

    return RedirectResponse(
        url="/login?error=Access%20denied.",
        status_code=303,
    )

if exc.status_code == 404:
    template = "errors/404.html"

    if _template_exists(template):
        return templates.TemplateResponse(
            request,
            template,
            _template_context(
                request,
                detail=exc.detail,
            ),
            status_code=404,
        )

    return RedirectResponse(
        url="/",
        status_code=303,
    )

template = "errors/500.html"

if _template_exists(template):
    return templates.TemplateResponse(
        request,
        template,
        _template_context(
            request,
            detail=exc.detail,
        ),
        status_code=exc.status_code,
    )

return {
    "error": exc.detail,
    "status_code": exc.status_code,
}
```

@app.exception_handler(
RequestValidationError,
)
async def validation_exception_handler(
request: Request,
exc: RequestValidationError,
):
logger.warning(
"Validation error on %s %s: %s",
request.method,
request.url.path,
exc.errors(),
)

```
template = "errors/400.html"

if _template_exists(template):
    return templates.TemplateResponse(
        request,
        template,
        _template_context(
            request,
            errors=exc.errors(),
            detail=(
                "Please check the information "
                "you entered."
            ),
        ),
        status_code=422,
    )

return {
    "error": "Validation error",
    "details": exc.errors(),
}
```

@app.exception_handler(Exception)
async def unhandled_exception_handler(
request: Request,
exc: Exception,
):
logger.exception(
"Unhandled BeatHub error on %s %s",
request.method,
request.url.path,
)

```
template = "errors/500.html"

if _template_exists(template):
    return templates.TemplateResponse(
        request,
        template,
        _template_context(
            request,
            detail=None,
        ),
        status_code=500,
    )

return {
    "error": "Internal server error",
    "status_code": 500,
}
```

@app.on_event("startup")
async def startup_event():
logger.info(
"BeatHub application started."
)

```
logger.info(
    "Storage backend: %s",
    getattr(
        settings,
        "MEDIA_STORAGE",
        "local",
    ),
)
```

@app.on_event("shutdown")
async def shutdown_event():
logger.info(
"BeatHub application shutting down."
)
