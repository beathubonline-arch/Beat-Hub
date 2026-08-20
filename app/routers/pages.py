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

    # Safely load creator tracks and albums
    tracks = list(getattr(profile, "tracks", None) or [])
    albums = list(getattr(profile, "albums", None) or [])

    # Public tracks:
    # - must be published
    # - exclusive tracks already sold are hidden
    public_tracks = []

    for track in tracks:
        if not getattr(track, "is_published", True):
            continue

        if getattr(track, "sales_model", None):
            sales_model = getattr(
                getattr(track, "sales_model", None),
                "value",
                getattr(track, "sales_model", None),
            )

            if (
                str(sales_model).lower() == "exclusive"
                and getattr(track, "is_sold", False)
            ):
                continue

        public_tracks.append(track)

    # Only published albums are public
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
