from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal

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
    recent_completed = db.query(func.count(Order.id)).filter(Order.created_at >= since, Order.status == OrderStatus.COMPLETED).scalar() or 0
    recent_gmv = db.query(func.coalesce(func.sum(Order.gross_amount), 0)).filter(Order.created_at >= since, Order.status == OrderStatus.COMPLETED).scalar() or 0
    latest_tracks = db.query(Track).filter(Track.is_published.is_(True)).order_by(Track.created_at.desc()).limit(20).all()
    track_items = [
        {"id": str(t.id), "title": t.title, "genre": t.genre, "bpm": t.bpm, "currency": t.currency,
         "price": _money(t.price), "sales_model": getattr(t.sales_model, "value", t.sales_model),
         "created_at": t.created_at.isoformat() if t.created_at else None}
        for t in latest_tracks
    ]
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z", "window": "last_7_days", "base_url": settings.BASE_URL,
        "totals": {"users": users, "creators": creators, "published_tracks": tracks, "profiles": profiles, "completed_orders_all_time": completed},
        "last_7_days": {"new_users": recent_users, "orders": recent_orders, "completed_orders": recent_completed, "completed_gmv": _money(recent_gmv)},
        "latest_tracks": track_items,
    }


def _catalog_terms(track: dict) -> set[str]:
    text = " ".join(str(track.get(k, "") or "") for k in ("title", "genre", "sales_model"))
    return {x for x in re.findall(r"[a-z0-9]+", text.lower()) if len(x) > 2}


def _request_terms(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(x) > 2}


def _zero_budget_plan(snapshot: dict) -> dict:
    totals = snapshot.get("totals", {})
    recent = snapshot.get("last_7_days", {})
    tracks = snapshot.get("latest_tracks", [])
    published = int(totals.get("published_tracks", 0) or 0)
    users = int(totals.get("users", 0) or 0)
    recent_users = int(recent.get("new_users", 0) or 0)
    recent_orders = int(recent.get("completed_orders", 0) or 0)
    if recent_orders == 0:
        priority = "Get the first qualified buyer conversation and prove the offer before chasing scale."
    elif published < 10:
        priority = "Increase useful catalog depth while turning existing users into repeat sharers."
    else:
        priority = "Double down on the content and referral loop producing qualified marketplace visits."
    beat = tracks[0].get("title") if tracks else "the strongest current BeatHub beat"
    return {
        "mode": "zero_budget",
        "diagnosis": f"BeatHub has {users} total users, {recent_users} new users in 7 days, {published} published tracks and {recent_orders} completed orders in 7 days.",
        "priority": priority,
        "daily_targets": [
            "Publish one useful creator-facing post with a single BeatHub CTA.",
            "Research 5 public, relevant creators manually and save only strong fits.",
            "Start 3 genuine conversations; no bulk or automated DMs.",
            "Ask every interested creator to play one specific beat and give feedback.",
        ],
        "prospects_to_seek": ["Independent artists actively releasing music", "Producers with public catalogs", "DJs and creator communities where music discovery is appropriate"],
        "content_experiments": [
            f"Beat-first short: use {beat} and show the sound before explaining BeatHub.",
            "Founder/product clip: explain in one sentence who BeatHub is for and what happens after purchase.",
            "Education clip: explain one licensing or beat-selection mistake independent artists make.",
        ],
        "outreach_angles": ["Personalized beat recommendation", "Useful answer to a public creator question", "Invitation to participate in a small creator challenge"],
        "product_loops": ["Producer shares their public store", "Artist shares a beat they discovered", "Post-purchase buyer shares the track/license outcome"],
        "metrics_to_watch": ["qualified visits", "signups", "beat plays", "checkout starts", "completed purchases", "referrals"],
        "kill_list": ["paid ads", "mass DMs", "bought followers", "automated unsolicited outreach", "scraping private data"],
        "tomorrow_test": "Run one beat-first short and one educational short; keep the winner based on qualified clicks, not vanity views.",
        "note": "This plan is generated locally from BeatHub's own database. No OpenAI API call, API key, or paid service is required.",
    }


async def run_growth_agent(snapshot: dict) -> dict:
    """Zero-budget Growth OS. Never requires an external AI API."""
    return _zero_budget_plan(snapshot)


async def scout_prospects(query: str, location: str = "Kenya", limit: int = 10) -> dict:
    """Return a manual scouting brief instead of paid/web scraping.

    We deliberately do not invent prospects or silently scrape private information.
    The admin can research public profiles manually and save verified URLs through /growth/prospects.
    """
    query, location = (query or "").strip(), (location or "Kenya").strip()
    limit = max(1, min(int(limit or 10), 20))
    if len(query) < 3:
        raise GrowthAgentError("Scout query must be at least 3 characters.")
    return {
        "prospects": [],
        "query": query,
        "location": location,
        "limit": limit,
        "mode": "zero_budget",
        "search_brief": [
            f"Search public artist/producer profiles for: {query}",
            f"Prioritize active creators in or relevant to {location}.",
            "Verify recent music activity and use only public creator/professional URLs.",
            "Save only prospects with a clear BeatHub fit; do not invent contacts or URLs.",
        ],
        "note": "Zero-budget mode does not call a paid AI/web-search API. Add verified public prospects manually after research.",
    }


async def match_beats(artist_request: str, tracks: list[dict], limit: int = 5) -> dict:
    """Deterministically match the supplied request to the supplied catalog."""
    request = (artist_request or "").strip()
    if len(request) < 3:
        raise GrowthAgentError("Artist request must be at least 3 characters.")
    limit = max(1, min(int(limit or 5), 10))
    request_terms = _request_terms(request)
    bpm_numbers = {int(x) for x in re.findall(r"\b([6-9][0-9]|1[0-9]{2})\s*bpm\b", request.lower())}
    ranked = []
    for track in tracks:
        if not isinstance(track, dict) or not str(track.get("id", "")).strip():
            continue
        terms = _catalog_terms(track)
        score = len(request_terms & terms) * 20
        genre = str(track.get("genre", "") or "").lower()
        if genre and any(term in genre for term in request_terms):
            score += 15
        bpm = track.get("bpm")
        try:
            bpm_int = int(bpm) if bpm is not None else None
        except (TypeError, ValueError):
            bpm_int = None
        if bpm_int and bpm_numbers:
            distance = min(abs(bpm_int - target) for target in bpm_numbers)
            score += max(0, 20 - min(distance, 20))
        ranked.append((score, track))
    ranked.sort(key=lambda item: (item[0], str(item[1].get("created_at", ""))), reverse=True)
    matches = []
    for score, track in ranked[:limit]:
        matches.append({
            "track_id": str(track["id"]),
            "title": track.get("title", "Untitled"),
            "score": min(100, max(1, score or 1)),
            "reason": "Catalog metadata matched the artist request; review the beat manually before recommending it.",
            "next_step": "Open the beat, listen, then send a personalized recommendation if it genuinely fits.",
        })
    return {"mode": "zero_budget", "matches": matches, "artist_request": request}


async def generate_outreach(prospect: dict, matches: list[dict]) -> dict:
    """Generate simple local outreach copy for human review."""
    if not isinstance(prospect, dict):
        raise GrowthAgentError("Prospect context is required.")
    public_url = str(prospect.get("public_url", "")).strip()
    if not public_url.startswith(("https://", "http://")):
        raise GrowthAgentError("Outreach requires a public prospect URL for human review.")
    name = str(prospect.get("name", "there")).strip() or "there"
    title = "a BeatHub beat"
    if matches and isinstance(matches[0], dict):
        title = str(matches[0].get("title") or title)
    short = f"Hey {name}, came across your work publicly and thought {title} might be worth a listen. No pressure — if the sound fits what you're making, I'd be happy to share the BeatHub link."
    warm = f"Hey {name} — I found your public profile while looking for independent creators whose sound could fit BeatHub. I had {title} in mind as a possible fit. If you want, have a listen and tell me honestly whether it's useful for your next record."
    return {
        "mode": "zero_budget",
        "context": "Generated locally from the supplied public prospect and catalog match; verify personalization before sending.",
        "message_short": short,
        "message_warm": warm,
        "follow_up": "One respectful follow-up after a reasonable interval only if the conversation is welcome; otherwise stop.",
    }


async def generate_content(track: dict) -> dict:
    """Generate reusable content prompts locally from track metadata."""
    if not isinstance(track, dict):
        raise GrowthAgentError("Track context is required.")
    title = str(track.get("title") or "this beat")
    genre = str(track.get("genre") or "music")
    bpm = track.get("bpm")
    bpm_text = f" at {bpm} BPM" if bpm else ""
    hooks = [
        f"If you're making {genre}, listen to the first 5 seconds of {title}.",
        f"Would you rap, sing or dance on {title}?",
        f"{title}: keep scrolling or press play?",
        f"This {genre} beat deserves an artist — is it you?",
        "Artists: what would you do with this sound?",
        "Producer drop: one beat, one challenge.",
        "Stop searching for a beat for 10 seconds and hear this.",
        f"Rate this {genre} beat from 1–10.",
        f"What kind of record would you make over {title}?",
        "Open verse challenge: your turn.",
    ]
    concepts = [
        f"Beat-first reveal of {title}{bpm_text}; put the strongest musical moment first.",
        f"Show an artist-style writing prompt over {title} without imitating a real artist.",
        f"Explain why {title} fits a {genre} record.",
        "A/B test two hooks over the same beat.",
        "Show search → listen → license → download as a simple creator workflow.",
        "Ask viewers to name the mood they hear.",
        "Producer breakdown: explain one production choice.",
        "30-second licensing explainer using this beat.",
        "Founder reaction to a first-time listener's feedback.",
        "Community challenge using the beat and a clear participation prompt.",
    ]
    return {
        "mode": "zero_budget",
        "hooks": hooks,
        "video_concepts": concepts,
        "captions": [
            f"{title} is live on BeatHub. Listen first, then decide if it fits your next record.",
            f"New {genre} energy. What would you create over {title}?",
            "Independent creators: discover, listen and license beats in one place.",
            "Your next record might start with one play.",
            "Producers upload. Artists discover. BeatHub connects the two.",
        ],
        "ctas": ["Listen on BeatHub", "Find your sound", "Open the beat", "Share with an artist", "Upload your next beat"],
        "creator_prompt": f"Create a short around {title}: lead with the sound, ask one specific creator question, then use one clear BeatHub CTA.",
    }
