"""Deterministic Phase 1 release-kit builder.

This deliberately produces useful campaign output without adding a second paid AI
provider. A later phase can replace this function with an LLM while preserving the
stored campaign schema and creator workflow.
"""
from __future__ import annotations


def build_release_kit(track, *, goal: str = "", audience: str = "", release_date: str = "", intelligence: dict | None = None) -> dict:
    title = (track.title or "New Release").strip()
    genre = (track.genre or "African music").strip()
    audience = (audience or f"listeners who love {genre}").strip()
    goal = (goal or "turn first listens into saves, shares and repeat plays").strip()

    intelligence = intelligence or {}\n    intel_angle = intelligence.get("campaign_angle", "")\n\n    positioning = (
        f"{title} is positioned as a {genre} release for {audience}. "
        f"The campaign should lead with one memorable feeling or moment from the track and {goal}. "
        "Keep every post recognizable: the same artwork, short visual language and one clear call to action."
    )
    hooks = [
        f"POV: you just found your next {genre} repeat.",
        f"Wait for the part in {title} that changes the whole mood.",
        f"If {genre} is your sound, save this before everyone finds it.",
        f"I made {title} for the people who feel music before they explain it.",
        f"Would you play this twice? Be ruthless.",
    ]
    captions = [
        f"{title} is here. First listen: headphones on. Save it if it earns a second play.",
        f"Built for {audience}. This is {title} — tell me the timestamp that caught you.",
        f"New music, no long speech. {title}. Listen, save, and send it to one person who gets the vibe.",
    ]
    video_ideas = [
        "Performance close-up: open on the strongest lyric/drop, then reveal the cover in the final two seconds.",
        "Studio-to-release transition: raw session clip → beat switch → finished artwork and release title.",
        "Reaction format: play the strongest 10–15 seconds and ask viewers to rate the moment before revealing the title.",
    ]
    rollout = [
        {"day": "Day -3", "action": "Post a 7–10 second teaser with Hook #1. Do not reveal the full track."},
        {"day": "Day -2", "action": "Show artwork or studio context and ask a simple either/or question to generate comments."},
        {"day": "Day -1", "action": "Post the strongest audio moment with the release date and a save/share CTA."},
        {"day": "Release day", "action": "Publish the hero Reel/Short, pin it, update bio/link, and reply to early comments quickly."},
        {"day": "Day +1", "action": "Post a second angle: performance, lyric meaning, production story, or audience reaction."},
        {"day": "Day +3", "action": "Reuse the best-performing hook with a different visual opening."},
        {"day": "Day +7", "action": "Review watch time, saves, shares and clicks; keep the winning creative pattern for the next release."},
    ]
    promo_copy = {
        "instagram": f"{title} — {genre}, made for {audience}. Out now. Save it, share it, and tell me which moment stays with you.",
        "tiktok": f"If {genre} is your lane, give {title} one listen. Which part should become the next video?",
        "youtube": f"{title} is a {genre} release created for {audience}. Listen through, leave your favorite timestamp, and subscribe for the next release.",
    }
    checklist = [
        "Confirm final master/audio and track title.",
        "Confirm cover artwork is square, clear and consistent with the campaign.",
        "Check artist/producer credits, genre, description and release date.",
        "Confirm licensing/ownership details before distribution.",
        "Prepare one bio/link destination for release traffic.",
        "Export at least three vertical promo clips before release day.",
        "Schedule the 7-day rollout and assign a CTA to every post.",
        "Record watch time, saves, shares, profile visits and sales/streams after launch.",
    ]
    return {
        "positioning": positioning,
        "hooks": hooks,
        "captions": captions,
        "video_ideas": video_ideas,
        "rollout": rollout,
        "promo_copy": promo_copy,
        "checklist": checklist,
        "release_date": release_date,
    }
