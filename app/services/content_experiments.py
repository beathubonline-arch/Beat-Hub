"""Deterministic short-form content experiment generation and comparison."""
from __future__ import annotations


def build_variants(track) -> list[dict]:
    title = (getattr(track, "title", None) or "this beat").strip()
    genre = (getattr(track, "genre", None) or "music").strip()
    hooks = [
        f"If you make {genre}, don't scroll before the drop in {title}.",
        f"Would you rap, sing or dance on {title}?",
        f"Artists: what record would you make with this {genre} sound?",
    ]
    visuals = [
        ("Performance / studio", "Open on a face or live studio action in frame one; cut on the strongest beat moment and end on the BeatHub beat page."),
        ("Artwork / motion", "Open on bold moving artwork or waveform text; reveal the beat title by second two and end on one clear BeatHub CTA."),
    ]
    return [
        {"hook_number": number, "hook_text": hook, "visual_treatment": visual, "shot_direction": direction}
        for number, hook in enumerate(hooks, start=1)
        for visual, direction in visuals
    ]


def variant_has_results(variant) -> bool:
    return any(float(getattr(variant, field, 0) or 0) > 0 for field in (
        "impressions", "avg_watch_time_seconds", "saves", "sends", "profile_visits", "registrations", "purchases"
    ))


def variant_score(variant) -> tuple:
    """Rank commercial outcomes first; use engagement only as a tie breaker."""
    return (
        int(getattr(variant, "purchases", 0) or 0),
        int(getattr(variant, "registrations", 0) or 0),
        int(getattr(variant, "profile_visits", 0) or 0),
        int(getattr(variant, "saves", 0) or 0) + int(getattr(variant, "sends", 0) or 0),
        float(getattr(variant, "avg_watch_time_seconds", 0) or 0),
    )


def strongest_variant(variants):
    measured = [variant for variant in variants if variant_has_results(variant)]
    return max(measured, key=variant_score) if measured else None
