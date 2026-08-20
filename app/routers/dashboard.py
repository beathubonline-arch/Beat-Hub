# ======================================================================
# MAIN DASHBOARD
# ======================================================================

@router.get("/dashboard")
@router.get("/dashboard/")
def dashboard_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    page: int = 1,
    q: str = "",
):
    """
    Main creator/admin dashboard.

    BUYERS ARE NEVER REDIRECTED TO /artist/dashboard HERE.
    This prevents an authentication/role redirect loop.
    """

    # --------------------------------------------------------------
    # BUYER
    # --------------------------------------------------------------
    if user.role == UserRole.BUYER:
        return templates.TemplateResponse(
            request,
            "artist_dashboard.html",
            ctx(
                request,
                user,
                profile=getattr(user, "profile", None),
                display_name=(
                    getattr(
                        getattr(user, "profile", None),
                        "stage_name",
                        None,
                    )
                    or user.email.split("@")[0]
                ),
                purchases=[],
                total_purchases=0,
                total_spent=Decimal("0"),
            ),
        )

    # --------------------------------------------------------------
    # ONLY CREATOR / ADMIN CONTINUE
    # --------------------------------------------------------------
    if user.role not in (
        UserRole.CREATOR,
        UserRole.ADMIN,
    ):
        return RedirectResponse(
            url="/account",
            status_code=303,
        )

    profile = user.profile

    if not profile:
        return RedirectResponse(
            url="/?error=Creator profile not found.",
            status_code=303,
        )

    stats = get_stats(
        db,
        profile.id,
    )

    track_count = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
        .count()
    )

    album_count = (
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile.id
        )
        .count()
    )

    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1

    if page < 1:
        page = 1

    track_per_page = 12

    tracks_query = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
    )

    q = (q or "").strip()

    if q:
        search_term = f"%{q}%"

        tracks_query = tracks_query.filter(
            Track.title.ilike(search_term)
            | Track.genre.ilike(search_term)
            | Track.tags.ilike(search_term)
        )

    track_total = tracks_query.count()

    track_total_pages = max(
        1,
        (
            track_total
            + track_per_page
            - 1
        )
        // track_per_page,
    )

    if page > track_total_pages:
        page = track_total_pages

    track_offset = (
        (page - 1)
        * track_per_page
    )

    tracks = (
        tracks_query
        .order_by(
            Track.created_at.desc()
        )
        .offset(track_offset)
        .limit(track_per_page)
        .all()
    )

    for track in tracks:
        track.cover_art_url = None

        if track.cover_art_path:
            try:
                track.cover_art_url = r2_presigned_url(
                    track.cover_art_path
                )
            except Exception:
                track.cover_art_url = None

    track_start = (
        track_offset + 1
        if track_total
        else 0
    )

    track_end = min(
        track_offset + len(tracks),
        track_total,
    )

    youtube_url = (
        "https://www.youtube.com/channel/"
        f"{settings.YOUTUBE_CHANNEL_ID}"
    )

    store_url = build_absolute_url(
        request,
        f"/profile/{profile.slug}",
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        ctx(
            request,
            user,
            profile=profile,
            stats=stats,
            track_count=track_count,
            album_count=album_count,
            tracks=tracks,
            track_page=page,
            track_total_pages=track_total_pages,
            track_total=track_total,
            track_total_count=track_total,
            track_per_page=track_per_page,
            track_search=q,
            track_start=track_start,
            track_end=track_end,
            q=q,
            youtube_url=youtube_url,
            discord_url=settings.DISCORD_INVITE_URL,
            store_url=store_url,
        ),
    )


# ======================================================================
# ARTIST / BUYER DASHBOARD
# ======================================================================

@router.get("/artist/dashboard")
@router.get("/artist/dashboard/")
def artist_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """
    Buyer/artist dashboard.

    IMPORTANT:
    This route does NOT redirect to /dashboard for buyers.
    It renders the dashboard directly, preventing redirect loops.
    """

    # --------------------------------------------------------------
    # CREATOR / ADMIN
    # --------------------------------------------------------------
    if user.role in (
        UserRole.CREATOR,
        UserRole.ADMIN,
    ):
        return RedirectResponse(
            url="/dashboard",
            status_code=303,
        )

    # --------------------------------------------------------------
    # BUYER
    # --------------------------------------------------------------
    if user.role != UserRole.BUYER:
        return RedirectResponse(
            url="/account",
            status_code=303,
        )

    # --------------------------------------------------------------
    # COMPLETED PURCHASES
    # --------------------------------------------------------------
    purchases = (
        db.query(Order)
        .filter(
            Order.buyer_id == user.id,
            Order.status == OrderStatus.COMPLETED,
        )
        .order_by(
            Order.completed_at.desc()
        )
        .all()
    )

    # --------------------------------------------------------------
    # DOWNLOAD URLS
    # --------------------------------------------------------------
    for purchase in purchases:

        purchase.download_url = None
        purchase.creator_name = None

        track = purchase.track

        if not track:
            continue

        creator_profile = getattr(
            track,
            "creator_profile",
            None,
        )

        if creator_profile:
            purchase.creator_name = (
                getattr(
                    creator_profile,
                    "stage_name",
                    None,
                )
                or "BeatHub Creator"
            )

        if track.audio_file_path:

            try:
                purchase.download_url = r2_presigned_url(
                    track.audio_file_path,
                    expires=3600,
                )
            except Exception:
                purchase.download_url = None

    # --------------------------------------------------------------
    # PURCHASE STATS
    # --------------------------------------------------------------
    total_purchases = len(purchases)

    total_spent = sum(
        (
            Decimal(
                str(
                    purchase.gross_amount or 0
                )
            )
            for purchase in purchases
        ),
        Decimal("0"),
    )

    # --------------------------------------------------------------
    # PROFILE
    # --------------------------------------------------------------
    profile = getattr(
        user,
        "profile",
        None,
    )

    display_name = (
        getattr(
            profile,
            "stage_name",
            None,
        )
        if profile
        else None
    )

    if not display_name:
        display_name = user.email.split("@")[0]

    # --------------------------------------------------------------
    # RENDER DIRECTLY
    # --------------------------------------------------------------
    return templates.TemplateResponse(
        request,
        "artist_dashboard.html",
        ctx(
            request,
            user,
            profile=profile,
            display_name=display_name,
            purchases=purchases,
            total_purchases=total_purchases,
            total_spent=total_spent,
        ),
    )
