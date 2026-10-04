import hashlib
import math
import random
import time
import wave
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.models.user import User
from app.utils.deps import require_creator

router = APIRouter(tags=["trend-dna"])
templates = Jinja2Templates(directory="app/templates")

GENERATED_DIR = Path("app/static/generated/trend-dna")
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

GENRE_PROFILES = {
    "afrobeats": {"bpm": 108, "swing": 0.08, "kick": [0, 6, 10, 14], "snare": [4, 12], "hat": [0, 2, 4, 6, 8, 10, 12, 14]},
    "amapiano": {"bpm": 113, "swing": 0.12, "kick": [0, 8], "snare": [4, 12], "hat": [2, 6, 10, 14]},
    "drill": {"bpm": 142, "swing": 0.03, "kick": [0, 7, 11], "snare": [4, 12], "hat": [0, 2, 4, 6, 8, 9, 10, 12, 14, 15]},
    "trap": {"bpm": 148, "swing": 0.02, "kick": [0, 6, 10, 13], "snare": [4, 12], "hat": list(range(16))},
    "dancehall": {"bpm": 102, "swing": 0.10, "kick": [0, 5, 10, 13], "snare": [4, 12], "hat": [0, 3, 6, 8, 11, 14]},
    "gengetone": {"bpm": 100, "swing": 0.09, "kick": [0, 5, 9, 13], "snare": [4, 12], "hat": [0, 2, 5, 7, 10, 12, 15]},
}

class TrendRequest(BaseModel):
    youtube_url: str = Field(min_length=8, max_length=500)
    genre: str = "afrobeats"
    mood: str = Field(default="energetic", max_length=40)
    energy: int = Field(default=85, ge=20, le=100)
    bpm: int | None = Field(default=None, ge=60, le=200)

def _ctx(request: Request, user: User, **extra):
    data = {
        "request": request,
        "current_user": user,
        "current_year": datetime.utcnow().year,
        "store_url": None,
    }
    profile = getattr(user, "profile", None)
    if profile and getattr(profile, "slug", None):
        data["store_url"] = f"/creator/{profile.slug}"
    data.update(extra)
    return data

def _validate_youtube(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        host = (parsed.netloc or "").lower()
        return parsed.scheme in {"http", "https"} and (
            host.endswith("youtube.com") or host.endswith("youtu.be")
        )
    except Exception:
        return False

async def _youtube_metadata(url: str) -> dict:
    endpoint = "https://www.youtube.com/oembed"
    try:
        async with httpx.AsyncClient(timeout=7.0, follow_redirects=True) as client:
            r = await client.get(endpoint, params={"url": url, "format": "json"})
            if r.status_code == 200:
                payload = r.json()
                return {
                    "title": str(payload.get("title") or "YouTube reference")[:160],
                    "author": str(payload.get("author_name") or "")[:120],
                    "thumbnail": str(payload.get("thumbnail_url") or ""),
                }
    except Exception:
        pass
    return {"title": "YouTube reference", "author": "", "thumbnail": ""}

def _osc(phase: float, kind: str = "sine") -> float:
    if kind == "square":
        return 1.0 if math.sin(phase) >= 0 else -1.0
    if kind == "saw":
        x = (phase / (2 * math.pi)) % 1.0
        return 2.0 * x - 1.0
    return math.sin(phase)

def _render_beat(path: Path, bpm: int, genre: str, energy: int, seed: int, variant: int):
    random.seed(seed + variant * 991)
    sr = 22050
    bars = 8
    beats_per_bar = 4
    seconds_per_beat = 60.0 / bpm
    duration = bars * beats_per_bar * seconds_per_beat
    total = int(sr * duration)
    samples = [0.0] * total
    profile = GENRE_PROFILES.get(genre, GENRE_PROFILES["afrobeats"])
    steps_per_bar = 16
    step_dur = seconds_per_beat / 4.0
    scale = [0, 3, 5, 7, 10] if "drill" in genre else [0, 2, 5, 7, 9]
    root = 55.0 if genre in {"drill", "trap"} else 65.41
    amp = 0.58 + (energy / 100.0) * 0.22

    def add_tone(start_s, dur_s, freq, vol, kind="sine", decay=5.0):
        start = int(start_s * sr)
        end = min(total, start + int(dur_s * sr))
        for i in range(start, end):
            t = (i - start) / sr
            env = math.exp(-decay * t / max(dur_s, 0.01))
            samples[i] += vol * env * _osc(2 * math.pi * freq * t, kind)

    def add_noise(start_s, dur_s, vol, decay=10.0):
        start = int(start_s * sr)
        end = min(total, start + int(dur_s * sr))
        for i in range(start, end):
            t = (i - start) / sr
            env = math.exp(-decay * t / max(dur_s, 0.01))
            samples[i] += vol * env * random.uniform(-1.0, 1.0)

    for bar in range(bars):
        for step in range(steps_per_bar):
            swing = profile["swing"] * step_dur if step % 2 else 0.0
            t = bar * beats_per_bar * seconds_per_beat + step * step_dur + swing

            if step in profile["kick"]:
                add_tone(t, 0.18, 62.0, 0.85 * amp, "sine", 8.5)
                add_tone(t, 0.05, 110.0, 0.25 * amp, "sine", 14.0)
            if step in profile["snare"]:
                add_noise(t, 0.10, 0.30 * amp, 13.0)
                add_tone(t, 0.08, 190.0, 0.16 * amp, "square", 12.0)
            if step in profile["hat"]:
                add_noise(t, 0.035, 0.09 * amp, 22.0)

        note_step = (bar + variant) % len(scale)
        bass_freq = root * (2 ** (scale[note_step] / 12.0))
        add_tone(bar * beats_per_bar * seconds_per_beat, seconds_per_beat * 1.6, bass_freq, 0.36 * amp, "sine", 2.8)

        if bar % 2 == 0:
            motif = scale[(bar // 2 + variant) % len(scale)]
            freq = 220.0 * (2 ** (motif / 12.0))
            add_tone(bar * beats_per_bar * seconds_per_beat + seconds_per_beat * 1.5, seconds_per_beat * 0.7, freq, 0.13 * amp, "saw", 3.2)

    peak = max(0.001, max(abs(x) for x in samples))
    gain = min(0.92 / peak, 1.0)

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        frames = bytearray()
        for x in samples:
            s = int(max(-1.0, min(1.0, x * gain)) * 32767)
            frames += int(s).to_bytes(2, byteorder="little", signed=True)
        wav.writeframes(frames)

def _blueprint(meta: dict, req: TrendRequest, bpm: int) -> dict:
    genre = req.genre if req.genre in GENRE_PROFILES else "afrobeats"
    energy = req.energy
    hook_seconds = 7 if energy >= 85 else 10 if energy >= 65 else 13
    return {
        "reference_title": meta["title"],
        "reference_author": meta["author"],
        "genre": genre,
        "bpm": bpm,
        "mood": req.mood,
        "energy": energy,
        "hook_entry_seconds": hook_seconds,
        "short_form_window": "0:07–0:24" if hook_seconds <= 7 else "0:10–0:28",
        "arrangement": [
            f"0:00–0:{hook_seconds:02d} micro-intro",
            f"0:{hook_seconds:02d} hook/drop",
            "8-bar artist pocket",
            "hook repeat with variation",
            "short turnaround for replay value",
        ],
        "originality_rule": "Uses the reference only for public metadata and high-level trend direction; no melody, lyrics, samples or recording are copied.",
        "scores": {
            "trend_match": min(96, 68 + round(energy * 0.27)),
            "hook_strength": min(95, 64 + round(energy * 0.30)),
            "short_form_readiness": min(97, 70 + round(energy * 0.26)),
            "artist_space": max(68, 96 - round(energy * 0.17)),
        },
    }

@router.get("/dashboard/trend-dna")
async def trend_dna_page(
    request: Request,
    user: User = Depends(require_creator),
):
    return templates.TemplateResponse(
        request,
        "trend_dna.html",
        _ctx(request, user),
    )

@router.post("/dashboard/trend-dna/generate")
async def trend_dna_generate(
    payload: TrendRequest,
    user: User = Depends(require_creator),
):
    if not _validate_youtube(payload.youtube_url):
        return JSONResponse({"ok": False, "error": "Paste a valid YouTube or youtu.be link."}, status_code=400)

    genre = payload.genre.lower().strip()
    if genre not in GENRE_PROFILES:
        genre = "afrobeats"

    meta = await _youtube_metadata(payload.youtube_url)
    bpm = payload.bpm or GENRE_PROFILES[genre]["bpm"]
    seed_raw = f"{payload.youtube_url}|{genre}|{payload.mood}|{payload.energy}|{getattr(user, 'id', 'u')}"
    seed = int(hashlib.sha256(seed_raw.encode()).hexdigest()[:12], 16)
    stamp = int(time.time())
    creator_id = str(getattr(user, "id", "creator")).replace("/", "_")

    urls = []
    names = ["Trend Match", "Commercial Flip", "Short-Form Cut"]
    for idx, name in enumerate(names, start=1):
        filename = f"{creator_id}-{stamp}-{idx}.wav"
        target = GENERATED_DIR / filename
        _render_beat(target, bpm + (idx - 2) * 2, genre, min(100, payload.energy + (idx - 2) * 4), seed, idx)
        urls.append({
            "name": name,
            "url": f"/static/generated/trend-dna/{filename}",
            "bpm": bpm + (idx - 2) * 2,
        })

    return {
        "ok": True,
        "metadata": meta,
        "blueprint": _blueprint(meta, payload, bpm),
        "drafts": urls,
        "upload_url": "/dashboard/upload",
    }
