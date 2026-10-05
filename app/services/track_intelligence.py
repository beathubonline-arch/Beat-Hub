"""Track Intelligence for BeatHub AI Career Agent Phase 2.

Uses existing metadata immediately and can enrich it with decoded audio features.
Analysis failures degrade safely to metadata instead of blocking creator campaigns.
"""
from __future__ import annotations

import os
from typing import Any


def _energy_label(rms: float | None) -> str:
    if rms is None:
        return "unknown"
    if rms < 0.045:
        return "low"
    if rms < 0.11:
        return "medium"
    return "high"


def _tempo_label(bpm: int | None) -> str:
    if not bpm:
        return "unknown"
    if bpm < 80:
        return "slow"
    if bpm < 115:
        return "mid-tempo"
    return "fast"


def analyse_track(track) -> dict[str, Any]:
    result = {
        "bpm": int(track.bpm) if getattr(track, "bpm", None) else None,
        "tempo": _tempo_label(getattr(track, "bpm", None)),
        "duration_seconds": None,
        "energy": "unknown",
        "rms": None,
        "brightness": None,
        "genre": (getattr(track, "genre", None) or "music").strip(),
        "source": "metadata",
    }
    path = getattr(track, "audio_file_path", None)
    if not path or not os.path.isfile(path):
        return result

    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(path, sr=22050, mono=True, duration=90)
        if y is None or len(y) < sr:
            return result
        result["duration_seconds"] = round(float(librosa.get_duration(y=y, sr=sr)), 1)
        rms = float(np.mean(librosa.feature.rms(y=y)))
        centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
        result["rms"] = round(rms, 4)
        result["energy"] = _energy_label(rms)
        result["brightness"] = "bright" if centroid >= 2500 else "warm"
        if not result["bpm"]:
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            value = float(np.asarray(tempo).reshape(-1)[0])
            if value > 0:
                result["bpm"] = int(round(value))
                result["tempo"] = _tempo_label(result["bpm"])
        result["source"] = "audio"
    except Exception:
        # Track Intelligence must never make release-campaign creation fail.
        pass
    return result


def campaign_angle(analysis: dict[str, Any]) -> str:
    bpm = analysis.get("bpm")
    tempo = analysis.get("tempo")
    energy = analysis.get("energy")
    genre = analysis.get("genre") or "music"
    parts = [genre]
    if bpm:
        parts.append(f"{bpm} BPM {tempo}")
    elif tempo != "unknown":
        parts.append(tempo)
    if energy != "unknown":
        parts.append(f"{energy}-energy")
    if analysis.get("brightness"):
        parts.append(analysis["brightness"])
    identity = ", ".join(parts)
    if energy == "high" or tempo == "fast":
        tactic = "Lead short-form content with movement, performance and the strongest drop or rhythmic switch."
    elif energy == "low" or tempo == "slow":
        tactic = "Lead with emotion, lyrics, close-up performance and a strong story around the song."
    else:
        tactic = "Test both performance and story-led clips, then repeat the format that earns the most saves and shares."
    return f"Track intelligence: {identity}. {tactic}"
