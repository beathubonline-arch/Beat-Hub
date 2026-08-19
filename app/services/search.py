"""
Real database-backed search across profiles (producers/artists/DJs),
tracks/beats and albums. Supports partial matching via SQL LIKE.
"""
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.music import Album, Track
from app.models.profile import Profile


def run_search(db: Session, query: str, limit: int = 8) -> dict:
    like = f"%{query.strip()}%"

    producers = (
        db.query(Profile)
        .filter(Profile.is_producer == True, Profile.stage_name.ilike(like))  # noqa: E712
        .limit(limit)
        .all()
    )
    djs = (
        db.query(Profile)
        .filter(Profile.is_dj == True, Profile.stage_name.ilike(like))  # noqa: E712
        .limit(limit)
        .all()
    )
    artists = (
        db.query(Profile)
        .filter(Profile.is_artist == True, Profile.stage_name.ilike(like))  # noqa: E712
        .limit(limit)
        .all()
    )
    tracks = (
        db.query(Track)
        .filter(Track.is_published == True, or_(Track.title.ilike(like), Track.tags.ilike(like), Track.genre.ilike(like)))  # noqa: E712
        .limit(limit)
        .all()
    )
    albums = (
        db.query(Album)
        .filter(Album.is_published == True, or_(Album.title.ilike(like), Album.genre.ilike(like)))  # noqa: E712
        .limit(limit)
        .all()
    )

    results = {
        "Producers": [{"title": p.stage_name, "subtitle": "Producer", "url": f"/profile/{p.slug}"} for p in producers],
        "Artists": [{"title": p.stage_name, "subtitle": "Artist", "url": f"/profile/{p.slug}"} for p in artists],
        "DJs": [{"title": p.stage_name, "subtitle": "DJ", "url": f"/profile/{p.slug}"} for p in djs],
        "Beats & Tracks": [
            {"title": t.title, "subtitle": t.creator_profile.stage_name, "url": f"/track/{t.slug}"} for t in tracks
        ],
        "Albums": [
            {"title": a.title, "subtitle": a.creator_profile.stage_name, "url": f"/album/{a.slug}"} for a in albums
        ],
    }
    total = sum(len(v) for v in results.values())
    return {"results": results, "total": total}
