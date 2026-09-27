"""Deterministic natural-language beat discovery without an external AI dependency."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


MOODS = {"dark", "happy", "sad", "romantic", "aggressive", "chill", "uplifting", "dreamy", "emotional", "energetic"}
ENERGIES = {"low", "medium", "high"}
GENRES = {"afrobeats", "amapiano", "drill", "trap", "hip hop", "r&b", "gengetone", "dancehall", "pop", "gospel"}
REGIONS = {"kenyan", "kenya", "nairobi", "african", "east african", "global", "uk", "us"}


@dataclass(frozen=True)
class BeatBrief:
    text: str
    bpm: int | None = None
    max_price: float | None = None
    mood: str | None = None
    energy: str | None = None
    genre: str | None = None
    region: str | None = None
    vocal_type: str | None = None


def _first_term(text: str, terms: set[str]) -> str | None:
    matches = [(match.start(), -len(term), term) for term in terms if (match := re.search(rf"\b{re.escape(term)}\b", text))]
    return min(matches)[2] if matches else None


def parse_brief(value: str) -> BeatBrief:
    text = " ".join(str(value or "").lower().split())[:500]
    bpm_match = re.search(r"\b(\d{2,3})\s*bpm\b", text)
    money_match = re.search(r"(?:ksh|kes|budget(?:\s+of)?|under|below|max(?:imum)?)\s*[:=]?\s*([\d,]+(?:\.\d{1,2})?)", text)
    vocal = None
    if re.search(r"\b(instrumental|no vocals?)\b", text): vocal = "instrumental"
    elif re.search(r"\b(female|woman|women)\s+vocals?\b", text): vocal = "female"
    elif re.search(r"\b(male|man|men)\s+vocals?\b", text): vocal = "male"
    elif "vocals" in text or "vocal" in text: vocal = "vocals"
    return BeatBrief(
        text=text,
        bpm=int(bpm_match.group(1)) if bpm_match else None,
        max_price=float(money_match.group(1).replace(",", "")) if money_match else None,
        mood=_first_term(text, MOODS), energy=_first_term(text, ENERGIES),
        genre=_first_term(text, GENRES), region=_first_term(text, REGIONS), vocal_type=vocal,
    )


def rank_tracks(tracks: Iterable[object], brief: BeatBrief, limit: int = 12) -> list[tuple[object, int, list[str]]]:
    ranked = []
    for track in tracks:
        try: price = float(getattr(track, "price", 0) or 0)
        except (TypeError, ValueError): price = 0
        if brief.max_price is not None and price > brief.max_price: continue
        score, reasons = 0, (["within budget"] if brief.max_price is not None else [])
        for attr, wanted, weight, label in (("genre", brief.genre, 5, "genre"), ("mood", brief.mood, 4, "mood"), ("energy", brief.energy, 3, "energy"), ("region", brief.region, 3, "region"), ("vocal_type", brief.vocal_type, 2, "vocal fit")):
            actual = str(getattr(track, attr, "") or "").lower()
            if wanted and (wanted in actual or actual in wanted): score += weight; reasons.append(f"{label}: {actual}")
        if brief.bpm is not None and getattr(track, "bpm", None):
            gap = abs(int(track.bpm) - brief.bpm)
            if gap <= 5: score += 5 - min(gap, 4); reasons.append(f"{track.bpm} BPM")
        haystack = " ".join(str(getattr(track, field, "") or "") for field in ("title", "description", "tags", "instruments", "intended_use", "similar_sound")).lower()
        tokens = {token for token in re.findall(r"[a-z0-9]+", brief.text) if len(token) > 3}
        overlap = sum(1 for token in tokens if token in haystack)
        score += min(overlap, 4)
        if overlap: reasons.append("matches your description")
        if score > 0 or not any((brief.genre, brief.mood, brief.energy, brief.region, brief.bpm, brief.vocal_type)):
            ranked.append((track, score, reasons[:4]))
    return sorted(ranked, key=lambda item: (item[1], getattr(item[0], "created_at", None) or datetime.min), reverse=True)[:limit]
