"""
BeatHub — main application entrypoint.

Run locally:
    uvicorn main:app --reload

Run in production (e.g. Render):
    uvicorn main:app --host 0.0.0.0 --port $PORT
"""
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import Base, engine
from app.routers import admin, auth, checkout, dashboard, mpesa_callback, music, pages

app = FastAPI(title=settings.APP_NAME)

templates = Jinja2Templates(directory="app/templates")

# Ensure media directory exists and is served (protected downloads are
# handled separately at the route level; this serves cover art / previews).
os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.MEDIA_ROOT), name="media")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Create tables on startup for convenience in fresh environments.
# For production, prefer `alembic upgrade head` as documented in README.md.
Base.metadata.create_all(bind=engine)

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(mpesa_callback.router)
app.include_router(dashboard.router)
app.include_router(admin.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return templates.TemplateResponse(request, 
            "errors/404.html", {"request": request, "current_user": None, "current_year": 2026}, status_code=404
        )
    if exc.status_code == 401:
        return RedirectResponse(url=f"/login?error=Please log in to continue.", status_code=303)
    if exc.status_code == 403:
        return templates.TemplateResponse(request, 
            "errors/403.html", {"request": request, "current_user": None, "current_year": 2026}, status_code=403
        )
    return templates.TemplateResponse(request, 
        "errors/500.html",
        {"request": request, "current_user": None, "current_year": 2026, "detail": exc.detail},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return templates.TemplateResponse(request, 
        "errors/400.html",
        {"request": request, "current_user": None, "current_year": 2026, "errors": exc.errors()},
        status_code=422,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak raw tracebacks to users. Log server-side, show a clean page.
    import logging
    logging.getLogger("beathub").exception("Unhandled error: %s", exc)
    return templates.TemplateResponse(request, 
        "errors/500.html",
        {"request": request, "current_user": None, "current_year": 2026, "detail": None},
        status_code=500,
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}
