# ============================================================
# PUBLIC CREATOR STORE
# ============================================================

@router.get("/store/{slug}")
def creator_store(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(
        get_optional_user
    ),
):
    """
    Public storefront for a creator (producer / DJ / artist).
    """

    profile = (
        db.query(Profile)
        .filter(Profile.slug == slug)
        .first()
    )

    if not profile:

        raise HTTPException(
            status_code=404,
            detail="Creator not found.",
        )

    # --------------------------------------------------------
    # Published tracks / albums only
    # --------------------------------------------------------

    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id,
            Track.is_published.is_(True),
        )
        .order_by(
            Track.created_at.desc()
        )
        .all()
    )

    albums = (
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile.id,
            Album.is_published.is_(True),
        )
        .order_by(
            Album.created_at.desc()
        )
        .all()
    )

    # --------------------------------------------------------
    # Avatar
    # --------------------------------------------------------

    avatar_url = None

    if profile.avatar_path:

        try:
            avatar_url = r2_url(
                profile.avatar_path,
                expires=3600,
            )
        except Exception:
            avatar_url = None

    return templates.TemplateResponse(
        request,
        "store.html",
        {
            "request": request,
            "current_user": user,
            "current_year": datetime.utcnow().year,
            "profile": profile,
            "tracks": tracks,
            "albums": albums,
            "avatar_url": avatar_url,
        },
    )
