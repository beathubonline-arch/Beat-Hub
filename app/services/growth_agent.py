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


def _extract_output_text(data: dict) -> str:
    text = data.get("output_text")
    if text:
        return str(text).strip()
    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
    return "\n".join(chunks).strip()


def _require_ai_config() -> tuple[str, str]:
    api_key = str(getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    model = str(getattr(settings, "OPENAI_MODEL", "") or "").strip()
    if not api_key:
        raise GrowthAgentError("OPENAI_API_KEY is not configured.")
    if not model:
        raise GrowthAgentError("OPENAI_MODEL is not configured.")
    return api_key, model


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
        {"id": str(t.id), "title": t.title, "genre": t.genre, "bpm": t.bpm, "currency": t.currency, "price": _money(t.price), "sales_model": getattr(t.sales_model, "value", t.sales_model), "created_at": t.created_at.isoformat() if t.created_at else None}
        for t in latest_tracks
    ]

    return {"generated_at": datetime.utcnow().isoformat() + "Z", "window": "last_7_days", "base_url": settings.BASE_URL,
            "totals": {"users": users, "creators": creators, "published_tracks": tracks, "profiles": profiles, "completed_orders_all_time": completed},
            "last_7_days": {"new_users": recent_users, "orders": recent_orders, "completed_orders": recent_completed, "completed_gmv": _money(recent_gmv)},
            "latest_tracks": track_items}


async def _responses_call(*, api_key: str, model: str, instructions: str, input_text: str, web_search: bool = False) -> dict:
    payload = {"model": model, "instructions": instructions, "input": input_text, "store": False}
    if web_search:
        payload["tools"] = [{"type": "web_search"}]
        payload["include"] = ["web_search_call.action.sources"]
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload)
    if response.status_code >= 400:
        detail = ""
        try: detail = str(response.json().get("error", {}).get("message", ""))[:300]
        except Exception: pass
        raise GrowthAgentError(f"OpenAI request failed ({response.status_code}). {detail}".strip())
    return response.json()


def _parse_json_response(data: dict, label: str) -> dict:
    text = _extract_output_text(data)
    if not text:
        raise GrowthAgentError(f"{label} returned no usable output.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GrowthAgentError(f"{label} returned malformed JSON.") from exc


async def run_growth_agent(snapshot: dict) -> dict:
    api_key, model = _require_ai_config()
    instructions = """You are BeatHub Growth Agent, a ruthless zero-budget growth operator for an early music marketplace.
Turn the supplied first-party snapshot into an actionable 24-hour plan. Never invent metrics. Never recommend buying ads/followers,
spam, scraping private data, unsolicited automated DMs, credential sharing or platform-rule evasion.
Prefer creator-to-creator loops, personalized outreach, UGC, short-form experiments, community participation and measurable referrals.
Return JSON keys: diagnosis, priority, daily_targets, prospects_to_seek, content_experiments, outreach_angles, product_loops,
metrics_to_watch, kill_list, tomorrow_test."""
    return _parse_json_response(await _responses_call(api_key=api_key, model=model, instructions=instructions, input_text=json.dumps(snapshot, ensure_ascii=False)), "Growth Agent")


async def scout_prospects(query: str, location: str = "Kenya", limit: int = 10) -> dict:
    api_key, model = _require_ai_config()
    query, location = (query or "").strip(), (location or "Kenya").strip()
    limit = max(1, min(int(limit or 10), 20))
    if len(query) < 3: raise GrowthAgentError("Scout query must be at least 3 characters.")
    instructions = f"""You are BeatHub Prospect Scout. Find public, relevant music-industry prospects using web search.
Target independent artists, producers, DJs, music creators, studios, campus artists and music communities. Location: {location}.
Return ONLY JSON: {{"prospects":[{{"name":"","type":"artist|producer|dj|creator|studio|community","platform":"","public_url":"","fit_score":0,"why_fit":"","recommended_angle":""}}]}}.
At most {limit} prospects. Use only public professional/creator information; never include private contact details, addresses or sensitive data.
Prefer active prospects with evidence of music activity. Do not invent URLs or facts. Recommended angle must be human and non-spammy."""
    result = _parse_json_response(await _responses_call(api_key=api_key, model=model, instructions=instructions, input_text=query, web_search=True), "Prospect Scout")
    if not isinstance(result.get("prospects"), list): raise GrowthAgentError("Prospect Scout returned an invalid prospect list.")
    result["prospects"] = result["prospects"][:limit]; result["query"] = query; result["location"] = location
    return result


async def match_beats(artist_request: str, tracks: list[dict], limit: int = 5) -> dict:
    api_key, model = _require_ai_config()
    limit = max(1, min(int(limit or 5), 10))
    if len((artist_request or '').strip()) < 3: raise GrowthAgentError("Artist request must be at least 3 characters.")
    instructions = f"""You are BeatHub Match. Match an artist's described sound to BeatHub's available beats.
Do not invent tracks. Only recommend IDs/titles present in the supplied catalog. Return JSON: {{"matches":[{{"track_id":"","title":"","score":0,"reason":"","next_step":""}}]}}.
Rank by musical fit, not price. At most {limit} matches."""
    result = _parse_json_response(await _responses_call(api_key=api_key, model=model, instructions=instructions, input_text=json.dumps({"artist_request": artist_request, "catalog": tracks}, ensure_ascii=False)), "Beat Matcher")
    result["matches"] = result.get("matches", [])[:limit]
    return result


async def generate_outreach(prospect: dict, matches: list[dict]) -> dict:
    api_key, model = _require_ai_config()
    instructions = """You write respectful, personalized BeatHub outreach for a founder.
Use only the supplied public prospect context and beat matches. Never claim you listened to something unless supplied.
Never pressure, impersonate, spam, or invent facts. Return JSON with keys: context, message_short, message_warm, follow_up."""
    return _parse_json_response(await _responses_call(api_key=api_key, model=model, instructions=instructions, input_text=json.dumps({"prospect": prospect, "matches": matches}, ensure_ascii=False)), "Outreach Generator")


async def generate_content(track: dict) -> dict:
    api_key, model = _require_ai_config()
    instructions = """You are BeatHub Content Engine. Turn one beat into organic short-form experiments.
Return JSON keys: hooks (10), video_concepts (10), captions (5), ctas (5), creator_prompt. Avoid copyrighted artist impersonation and deceptive claims.
Optimize for curiosity, participation and creator-to-creator discovery rather than generic advertising."""
    return _parse_json_response(await _responses_call(api_key=api_key, model=model, instructions=instructions, input_text=json.dumps(track, ensure_ascii=False)), "Content Engine")
