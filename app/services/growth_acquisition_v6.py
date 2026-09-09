from __future__ import annotations

import asyncio
import html
import logging
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from sqlalchemy.orm import Session

from app.models.growth import GrowthProspect, GrowthTouch
from app.models.music import Track
from app.services.growth_agent import generate_outreach, match_beats

logger = logging.getLogger("beathub.growth_acquisition")

DDG_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
BING_URL = "https://www.bing.com/search"
ALLOWED_HOSTS = (
    "instagram.com",
    "tiktok.com",
    "youtube.com",
    "soundcloud.com",
    "audiomack.com",
    "bandcamp.com",
)
BLOCKED_PATH_PARTS = (
    "/explore",
    "/search",
    "/hashtag",
    "/tag/",
    "/music/",
    "/results",
)


def _strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value or ""))).strip()


def _unwrap_ddg_url(value: str) -> str:
    value = html.unescape(value or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlparse(value)
    if "duckduckgo.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        if qs.get("uddg"):
            value = unquote(qs["uddg"][0])
    return value


def _host_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().split(":")[0]
        host = host[4:] if host.startswith("www.") else host
        if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
            return False
        path = parsed.path.lower()
        if any(part in path for part in BLOCKED_PATH_PARTS):
            return False
        if host.endswith("youtube.com") and not any(x in path for x in ("/channel/", "/@", "/c/", "/user/")):
            return False
        if host.endswith("tiktok.com") and "/@" not in path:
            return False
        if host.endswith("instagram.com"):
            segments = [segment for segment in path.split("/") if segment]
            if len(segments) != 1 or segments[0] in {"accounts", "about", "developer", "legal"}:
                return False
        return True
    except Exception:
        return False


def _platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "instagram" in host:
        return "instagram"
    if "tiktok" in host:
        return "tiktok"
    if "youtube" in host:
        return "youtube"
    if "soundcloud" in host:
        return "soundcloud"
    if "audiomack" in host:
        return "audiomack"
    if "bandcamp" in host:
        return "bandcamp"
    return "web"


def _name_from_result(title: str, url: str) -> str:
    clean = _strip_tags(title)
    clean = re.split(r"\s+[|\-–—]\s+", clean)[0].strip()
    if clean:
        return clean[:255]
    parsed = urlparse(url)
    bits = [p for p in parsed.path.split("/") if p and p not in {"channel", "user", "c"}]
    if bits:
        return bits[0].lstrip("@").replace("-", " ").replace("_", " ")[:255]
    return parsed.netloc[:255]


def _fit_score(title: str, snippet: str, location: str) -> tuple[int, str]:
    text = f"{title} {snippet}".lower()
    score = 20
    reasons = []
    signals = {
        "artist": 15,
        "rapper": 18,
        "singer": 18,
        "songwriter": 14,
        "musician": 12,
        "producer": 10,
        "new music": 12,
        "single": 8,
        "ep": 6,
        "album": 6,
        "official": 5,
        "2026": 10,
        "2025": 5,
    }
    for token, weight in signals.items():
        if token in text:
            score += weight
            reasons.append(token)
    if location and location.lower() in text:
        score += 15
        reasons.append(location.lower())
    score = max(1, min(100, score))
    why = "Public profile shows " + ", ".join(reasons[:5]) if reasons else "Public music profile discovered from a targeted artist search."
    return score, why


def _result_item(raw_url: str, title: str, snippet: str, location: str) -> dict | None:
    url = _unwrap_ddg_url(raw_url)
    if not _host_allowed(url):
        return None
    normalized = url.split("?")[0].rstrip("/")
    title = _strip_tags(title)
    snippet = _strip_tags(snippet)
    score, why = _fit_score(title, snippet, location)
    return {
        "name": _name_from_result(title, normalized),
        "public_url": normalized,
        "platform": _platform(normalized),
        "prospect_type": "artist",
        "location": location,
        "fit_score": score,
        "why_fit": why,
        "recommended_angle": "Personalized beat recommendation based on recent public music activity.",
        "snippet": snippet[:500],
    }


def _parse_results(markup: str, location: str) -> list[dict]:
    links = re.findall(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        markup or "",
        flags=re.I | re.S,
    )
    snippets = re.findall(
        r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>',
        markup or "",
        flags=re.I | re.S,
    )
    snippet_texts = [_strip_tags(a or b) for a, b in snippets]
    results, seen = [], set()
    for idx, (raw_url, title_html) in enumerate(links):
        item = _result_item(raw_url, title_html, snippet_texts[idx] if idx < len(snippet_texts) else "", location)
        if not item or item["public_url"] in seen:
            continue
        seen.add(item["public_url"])
        results.append(item)
    return results


def _parse_bing_results(markup: str, location: str) -> list[dict]:
    blocks = re.findall(r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', markup or "", flags=re.I | re.S)
    results, seen = [], set()
    for block in blocks:
        link = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.I | re.S)
        if not link:
            continue
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, flags=re.I | re.S)
        item = _result_item(link.group(1), link.group(2), snippet_match.group(1) if snippet_match else "", location)
        if not item or item["public_url"] in seen:
            continue
        seen.add(item["public_url"])
        results.append(item)
    return results


def _parse_lite_results(markup: str, location: str) -> list[dict]:
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', markup or "", flags=re.I | re.S)
    results, seen = [], set()
    for raw_url, title_html in links:
        item = _result_item(raw_url, title_html, "", location)
        if not item or item["public_url"] in seen:
            continue
        seen.add(item["public_url"])
        results.append(item)
    return results


async def _search_query(client: httpx.AsyncClient, query: str, location: str) -> tuple[list[dict], str]:
    providers = (
        ("ddg_html", "POST", DDG_URL, {"data": {"q": query}}, _parse_results),
        ("bing", "GET", BING_URL, {"params": {"q": query, "count": "20"}}, _parse_bing_results),
        ("ddg_lite", "GET", f"{DDG_LITE_URL}?q={quote_plus(query)}", {}, _parse_lite_results),
    )
    failures = []
    for name, method, url, kwargs, parser in providers:
        try:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            parsed = parser(response.text, location)
            if parsed:
                logger.info("[Growth Acquisition] provider=%s query=%r results=%s", name, query, len(parsed))
                return parsed, name
            failures.append(f"{name}:empty")
        except Exception as exc:
            failures.append(f"{name}:{type(exc).__name__}")
    logger.warning("[Growth Acquisition] all providers failed/empty query=%r providers=%s", query, ",".join(failures))
    return [], "none"


async def discover_public_prospects(location: str = "Kenya", limit: int = 10) -> list[dict]:
    """Discover public creator profiles without paid APIs or private-data scraping."""
    limit = max(1, min(int(limit or 10), 20))
    queries = [
        f'"independent artist" {location} Instagram new music',
        f'rapper singer {location} TikTok new music',
        f'musician {location} YouTube official artist 2026',
        f'artist {location} SoundCloud Audiomack new single',
        f'site:instagram.com {location} rapper "new music"',
        f'site:tiktok.com/@ {location} singer artist',
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    found: list[dict] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True, headers=headers) as client:
        for query in queries:
            if len(found) >= limit * 2:
                break
            rows, _provider = await _search_query(client, query, location)
            for item in rows:
                if item["public_url"] in seen:
                    continue
                seen.add(item["public_url"])
                found.append(item)
    found.sort(key=lambda item: item.get("fit_score", 0), reverse=True)
    return found[:limit]


def _track_payload(track: Track) -> dict:
    return {
        "id": str(track.id),
        "title": track.title,
        "genre": track.genre,
        "bpm": track.bpm,
        "currency": track.currency,
        "price": float(track.price or 0),
        "sales_model": getattr(track.sales_model, "value", track.sales_model),
        "created_at": track.created_at.isoformat() if track.created_at else None,
    }


async def build_daily_acquisition_queue(db: Session, location: str = "Kenya", limit: int = 8) -> dict:
    """Discover, qualify, save, match and draft a human-approved outreach queue."""
    discovered = await discover_public_prospects(location=location, limit=max(limit * 2, 10))
    tracks = db.query(Track).filter(Track.is_published.is_(True)).order_by(Track.created_at.desc()).all()
    track_payloads = [_track_payload(t) for t in tracks]
    if not track_payloads:
        return {"ok": True, "status": "no_catalog", "discovered": len(discovered), "queue": []}

    queue = []
    skipped = 0
    for item in discovered:
        if len(queue) >= limit:
            break
        if int(item.get("fit_score") or 0) < 45:
            skipped += 1
            continue
        prospect = db.query(GrowthProspect).filter(GrowthProspect.public_url == item["public_url"]).first()
        if prospect and prospect.status not in {"found", "qualified", "matched", "outreach_ready"}:
            skipped += 1
            continue
        if prospect is None:
            prospect = GrowthProspect(
                name=item["name"],
                prospect_type=item["prospect_type"],
                platform=item["platform"],
                public_url=item["public_url"],
                location=item["location"],
                fit_score=item["fit_score"],
                why_fit=item["why_fit"],
                recommended_angle=item["recommended_angle"],
                status="qualified",
            )
            db.add(prospect)
            db.flush()
            db.add(GrowthTouch(
                prospect_id=prospect.id,
                stage="qualified",
                channel=prospect.platform,
                note="Automatically discovered from a public web search; human review required before contact.",
            ))
        else:
            prospect.fit_score = max(int(prospect.fit_score or 0), int(item["fit_score"]))
            prospect.why_fit = item["why_fit"]
            if prospect.status == "found":
                prospect.status = "qualified"

        artist_request = f"{prospect.name} {item.get('snippet', '')} {location}"
        matches = await match_beats(artist_request, track_payloads, limit=3)
        match_list = matches.get("matches", [])
        if not match_list:
            skipped += 1
            continue
        best = match_list[0]
        prospect.matched_track_id = str(best["track_id"])
        prospect.status = "outreach_ready"
        db.add(GrowthTouch(
            prospect_id=prospect.id,
            stage="matched",
            channel="BeatHub",
            note=f"Auto-matched to published track {best['track_id']}; human listen/review still required.",
        ))
        outreach = await generate_outreach(
            {"name": prospect.name, "public_url": prospect.public_url, "platform": prospect.platform},
            match_list,
        )
        db.add(GrowthTouch(
            prospect_id=prospect.id,
            stage="outreach_ready",
            channel=prospect.platform,
            note=outreach.get("message_short", "")[:1000],
        ))
        queue.append({
            "prospect_id": prospect.id,
            "name": prospect.name,
            "platform": prospect.platform,
            "public_url": prospect.public_url,
            "fit_score": prospect.fit_score,
            "why_fit": prospect.why_fit,
            "track_id": best["track_id"],
            "track_title": best["title"],
            "match_score": best["score"],
            "message_short": outreach.get("message_short"),
            "message_warm": outreach.get("message_warm"),
            "status": "outreach_ready",
        })
    db.commit()
    return {
        "ok": True,
        "status": "ready" if queue else "no_qualified_results",
        "mode": "zero_budget_public_search",
        "location": location,
        "discovered": len(discovered),
        "qualified_queue": len(queue),
        "skipped": skipped,
        "queue": queue,
        "human_approval_required": True,
        "note": "The runner only uses public profile URLs and never sends unsolicited messages automatically.",
    }


def run_acquisition_queue(db: Session, location: str = "Kenya", limit: int = 8) -> dict:
    return asyncio.run(build_daily_acquisition_queue(db, location=location, limit=limit))
