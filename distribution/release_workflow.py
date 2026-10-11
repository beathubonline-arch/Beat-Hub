"""Isolated BeatHub Distribution draft validation. No DSP delivery or billing."""
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

class Status(str, Enum):
    DRAFT = "DRAFT"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    IN_REVIEW = "IN_REVIEW"
    APPROVED_FOR_PARTNER = "APPROVED_FOR_PARTNER"
    REJECTED = "REJECTED"

@dataclass
class Track:
    title: str
    audio_key: str
    writers: list[str]
    explicit: bool = False
    isrc: Optional[str] = None

@dataclass
class Release:
    creator_id: str
    title: str
    artist: str
    kind: str
    artwork_key: str
    release_date: date
    rights_confirmed: bool = False
    tracks: list[Track] = field(default_factory=list)
    status: Status = Status.DRAFT

def validate_release(release: Release) -> list[str]:
    errors = []
    if release.kind not in ("single", "ep", "album"):
        errors.append("Invalid release type")
    if not release.title.strip() or not release.artist.strip():
        errors.append("Release title and artist required")
    if not release.artwork_key.strip():
        errors.append("Artwork required")
    if not release.rights_confirmed:
        errors.append("Copyright and distribution rights must be confirmed")
    if not release.tracks:
        errors.append("At least one track required")
    if release.kind == "single" and len(release.tracks) != 1:
        errors.append("Singles require exactly one track")
    for index, track in enumerate(release.tracks, 1):
        if not track.title.strip() or not track.audio_key.strip() or not any(w.strip() for w in track.writers):
            errors.append(f"Track {index} requires title, audio and writer credits")
    return errors

def submit_for_review(release: Release, actor_creator_id: str) -> Release:
    if not actor_creator_id or actor_creator_id != release.creator_id:
        raise PermissionError("Not release owner")
    if release.status not in (Status.DRAFT, Status.NEEDS_CHANGES):
        raise ValueError("Release cannot be submitted from current state")
    errors = validate_release(release)
    if errors:
        raise ValueError("; ".join(errors))
    release.status = Status.IN_REVIEW
    return release
