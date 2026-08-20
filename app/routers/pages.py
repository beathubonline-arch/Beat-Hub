@router.get("/profile/{slug}")
def public_profile(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    profile = (
        db.query(Profile)
        .filter(Profile.slug == slug)
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Creator profile not found.",
        )

    tracks = list(profile.tracks or [])
    albums = list(profile.albums or [])

    # Only show published tracks.
    # Exclusive tracks that have been sold are hidden.
    public_tracks = [
        track
        for track in tracks
        if getattr(track, "is_published", True)
        and not getattr(track, "is_sold", False)
    ]

    public_albums = [
        album
        for album in albums
        if getattr(album, "is_published", True)
    ]

    return templates.TemplateResponse(
        request,
        "profile.html",
        ctx(
            request,
            current_user,
            profile=profile,
            creator=getattr(profile, "user", None),
            tracks=public_tracks,
            albums=public_albums,
        ),
    )
