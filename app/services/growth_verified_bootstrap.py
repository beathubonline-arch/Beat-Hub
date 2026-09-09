from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from app.models.growth import GrowthProspect, GrowthTouch
from app.models.music import Track
from app.services.growth_agent import generate_outreach, match_beats

# Public creator profiles verified from public search results on 2026-09-09.
# This is a resilience bootstrap only: live discovery is always attempted first.
# No private contact data is stored or used.
VERIFIED_PUBLIC_PROSPECTS = [
    {
        "name": "Yung-Issa",
        "prospect_type": "artist",
        "platform": "soundcloud",
        "public_url": "https://soundcloud.com/brokeboiissa",
        "location": "Nairobi, Kenya",
        "fit_score": 95,
        "why_fit": "Public SoundCloud profile identifies Yung-Issa as an independent upcoming Kenyan artist in Nairobi, with new releases published in April and May 2026.",
        "snippet": "Kenyan independent artist, hip-hop, recent 2026 releases.",
    },
    {
        "name": "Karun",
        "prospect_type": "artist",
        "platform": "soundcloud",
        "public_url": "https://soundcloud.com/karunmusic",
        "location": "Nairobi, Kenya",
        "fit_score": 88,
        "why_fit": "Public SoundCloud profile identifies Karun as a Nairobi R&B/Soul singer, songwriter and producer with an active independent music catalogue.",
        "snippet": "Nairobi R&B soul singer songwriter producer artist.",
    },
    {
        "name": "The S3cr3t",
        "prospect_type": "artist",
        "platform": "soundcloud",
        "public_url": "https://soundcloud.com/smoothaysmooth2",
        "location": "Nairobi, Kenya",
        "fit_score": 84,
        "why_fit": "Public SoundCloud profile identifies The S3cr3t as a Kenyan Nairobi artist focused on rap, with multiple independent projects and a 2025 release visible publicly.",
        "snippet": "Kenyan Nairobi rap artist independent projects.",
    },
    {
        "name": "THEURI",
        "prospect_type": "artist",
        "platform": "soundcloud",
        "public_url": "https://soundcloud.com/theuri",
        "location": "Nairobi, Kenya",
        "fit_score": 80,
        "why_fit": "Public SoundCloud profile identifies THEURI as a Nairobi rapper, singer-songwriter, multi-instrumentalist and producer working across Afro-fusion, R&B, neo-soul and rap.",
        "snippet": "Nairobi rapper singer songwriter Afro fusion R&B neo soul rap.",
    },
    {
        "name": "sELO",
        "prospect_type": "artist",
        "platform": "soundcloud",
        "public_url": "https://soundcloud.com/onyandos",
        "location": "Nairobi, Kenya",
        "fit_score": 79,
        "why_fit": "Public SoundCloud profile describes sELO as a Kenyan artist exploring East African sounds, with public live material posted in 2025.",
        "snippet": "Kenyan artist East African sounds Nairobi live music.",
    },
    {
        "name": "twopiecemadeit",
        "prospect_type": "producer",
        "platform": "soundcloud",
        "public_url": "https://soundcloud.com/prodbytwopiece",
        "location": "Nairobi, Kenya",
        "fit_score": 76,
        "why_fit": "Public SoundCloud profile is based in Nairobi and shows an active 2026 production catalogue, making it a relevant creator-supply prospect for BeatHub.",
        "snippet": "Nairobi producer active 2026 catalogue hoodtrap beats.",
    },
    {
        "name": "DJ Pro D",
        "prospect_type": "producer",
        "platform": "soundcloud",
        "public_url": "https://soundcloud.com/producerdeno",
        "location": "Nairobi, Kenya",
        "fit_score": 72,
        "why_fit": "Public SoundCloud profile identifies DJ Pro D as a Nairobi DJ/producer with a Kenyan trap mix published in August 2026.",
        "snippet": "Nairobi DJ producer Kenyan trap 2026.",
    },
]


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


async def build_verified_bootstrap_queue(db: Session, limit: int = 8) -> dict:
    tracks = db.query(Track).filter(Track.is_published.is_(True)).order_by(Track.created_at.desc()).all()
    track_payloads = [_track_payload(track) for track in tracks]
    if not track_payloads:
        return {"ok": True, "status": "no_catalog", "qualified_queue": 0, "queue": []}

    queue = []
    for item in VERIFIED_PUBLIC_PROSPECTS:
        if len(queue) >= max(1, min(limit, 20)):
            break

        prospect = db.query(GrowthProspect).filter(GrowthProspect.public_url == item["public_url"]).first()
        if prospect and prospect.status not in {"found", "qualified", "matched", "outreach_ready"}:
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
                recommended_angle="Personalized BeatHub recommendation based on the creator's public music profile.",
                status="qualified",
            )
            db.add(prospect)
            db.flush()
            db.add(GrowthTouch(
                prospect_id=prospect.id,
                stage="qualified",
                channel=prospect.platform,
                note="Loaded from BeatHub's web-verified public-profile bootstrap because live search providers were unavailable; human review required before contact.",
            ))
        else:
            prospect.fit_score = max(int(prospect.fit_score or 0), int(item["fit_score"]))
            prospect.why_fit = item["why_fit"]
            if prospect.status == "found":
                prospect.status = "qualified"

        matches = await match_beats(
            f"{item['name']} {item['snippet']} {item['location']}",
            track_payloads,
            limit=3,
        )
        match_list = matches.get("matches", [])
        if not match_list:
            continue
        best = match_list[0]
        prospect.matched_track_id = str(best["track_id"])
        prospect.status = "outreach_ready"
        db.add(GrowthTouch(
            prospect_id=prospect.id,
            stage="matched",
            channel="BeatHub",
            note=f"Matched to published track {best['track_id']} from verified public-profile bootstrap; human listen/review required.",
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
        "mode": "verified_public_bootstrap",
        "location": "Nairobi, Kenya",
        "discovered": len(VERIFIED_PUBLIC_PROSPECTS),
        "qualified_queue": len(queue),
        "skipped": len(VERIFIED_PUBLIC_PROSPECTS) - len(queue),
        "queue": queue,
        "human_approval_required": True,
        "note": "Live search was unavailable, so BeatHub used a small set of previously web-verified public creator profiles. No messages were sent automatically.",
    }


def run_verified_bootstrap_queue(db: Session, limit: int = 8) -> dict:
    return asyncio.run(build_verified_bootstrap_queue(db, limit=limit))
