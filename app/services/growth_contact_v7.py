from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
GENERIC_BLOCKLIST = {"example.com", "sentry.io", "w3.org", "schema.org"}
DIRECT_DM_PLATFORMS = {"instagram", "tiktok"}


def _platform(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    for name in ("instagram", "tiktok", "soundcloud", "audiomack", "bandcamp", "youtube"):
        if name in host:
            return name
    return "web"


def _clean_email(value: str) -> str | None:
    email = (value or "").strip().strip(".,;:()[]<>\"'").lower()
    if not EMAIL_RE.fullmatch(email):
        return None
    domain = email.rsplit("@", 1)[-1]
    if domain in GENERIC_BLOCKLIST or email.endswith(("@example.com", "@email.com")):
        return None
    return email


async def verify_public_contact(public_url: str, platform: str | None = None) -> dict:
    """Find a contact route exposed on the prospect's public page.

    This intentionally does not guess addresses, enrich from private data, or send anything.
    A public email is preferred. Instagram/TikTok profile URLs are accepted as public DM routes.
    Other music profile URLs remain contact_research until an explicit contact route is found.
    """
    public_url = str(public_url or "").strip()
    platform = (platform or _platform(public_url)).lower()
    if not public_url.startswith(("https://", "http://")):
        return {"verified": False, "status": "contact_research", "reason": "No valid public profile URL."}

    try:
        headers = {"User-Agent": "Mozilla/5.0 BeatHubGrowth/1.0", "Accept": "text/html,*/*"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=True, headers=headers) as client:
            response = await client.get(public_url)
            if response.status_code < 400:
                text = response.text[:1_000_000]
                emails = []
                for raw in EMAIL_RE.findall(text):
                    email = _clean_email(raw)
                    if email and email not in emails:
                        emails.append(email)
                if emails:
                    return {
                        "verified": True,
                        "status": "contact_verified",
                        "contact_type": "email",
                        "contact_value": emails[0],
                        "contact_url": f"mailto:{emails[0]}",
                        "source_url": str(response.url),
                        "reason": "Email is publicly exposed on the creator's public profile/page.",
                    }
    except Exception:
        pass

    if platform in DIRECT_DM_PLATFORMS:
        return {
            "verified": True,
            "status": "contact_verified",
            "contact_type": "dm",
            "contact_value": platform,
            "contact_url": public_url,
            "source_url": public_url,
            "reason": f"Public {platform.title()} profile provides the manual DM route.",
        }

    return {
        "verified": False,
        "status": "contact_research",
        "contact_type": None,
        "contact_value": None,
        "contact_url": public_url,
        "source_url": public_url,
        "reason": "Public music profile found, but no verified email or supported direct-DM route was exposed.",
    }
