from __future__ import annotations

from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Track
from app.models.profile import Profile

router = APIRouter(tags=["seo"])


def _base_url(request: Request) -> str:
    configured = str(getattr(settings, "BASE_URL", "") or "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def _public_track(track: Track) -> bool:
    if getattr(track, "is_published", True) is False:
        return False
    sales_model = getattr(track, "sales_model", None)
    sales_value = getattr(sales_model, "value", sales_model)
    if str(sales_value or "").strip().lower() == "exclusive" and getattr(track, "is_sold", False):
        return False
    return bool(getattr(track, "slug", None))


@router.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    base = _base_url(request)
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /dashboard",
        "Disallow: /account",
        "Disallow: /checkout",
        "Disallow: /notifications",
        "Disallow: /login",
        "Disallow: /signup",
        "Disallow: /forgot-password",
        "Disallow: /reset-password",
        "Disallow: /api/",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ])
    return PlainTextResponse(body, media_type="text/plain")


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request, db: Session = Depends(get_db)):
    base = _base_url(request)
    urls: list[tuple[str, str, str]] = [
        ("/", "daily", "1.0"),
        ("/beats", "daily", "0.9"),
        ("/tracks", "daily", "0.8"),
        ("/hot-picks", "daily", "0.8"),
        ("/sessions", "weekly", "0.7"),
        ("/merch", "daily", "0.7"),
        ("/support", "monthly", "0.4"),
        ("/terms", "yearly", "0.2"),
    ]

    try:
        tracks = db.query(Track).all()
        for track in tracks:
            if _public_track(track):
                urls.append((f"/track/{track.slug}", "weekly", "0.8"))
    except Exception:
        pass

    try:
        profiles = db.query(Profile).all()
        for profile in profiles:
            slug = str(getattr(profile, "slug", "") or "").strip()
            if slug:
                urls.append((f"/store/{slug}", "weekly", "0.8"))
    except Exception:
        pass

    seen = set()
    entries = []
    for path, changefreq, priority in urls:
        loc = f"{base}{path}"
        if loc in seen:
            continue
        seen.add(loc)
        entries.append(
            "<url>"
            f"<loc>{escape(loc)}</loc>"
            f"<changefreq>{changefreq}</changefreq>"
            f"<priority>{priority}</priority>"
            "</url>"
        )

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + \
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
        "\n".join(entries) + "\n</urlset>\n"
    return Response(content=xml, media_type="application/xml")
