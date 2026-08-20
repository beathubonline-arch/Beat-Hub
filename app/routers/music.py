# ----------------------------------------------------------------------
# PURCHASE DOWNLOAD
# ----------------------------------------------------------------------

@router.get("/download/track/{track_ref}")
@router.get("/download/{track_ref}")
def download_track(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    track_ref = str(track_ref).strip()

    # --------------------------------------------------------------
    # Find track by UUID first, then slug.
    # --------------------------------------------------------------

    track = None

    try:
        track = (
            db.query(Track)
            .filter(Track.id == track_ref)
            .first()
        )
    except Exception:
        track = None

    if not track:
        track = (
            db.query(Track)
            .filter(Track.slug == track_ref)
            .first()
        )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    # --------------------------------------------------------------
    # Verify completed purchase.
    # --------------------------------------------------------------

    license_record = (
        db.query(License)
        .join(
            Order,
            License.order_id == Order.id,
        )
        .filter(
            License.buyer_id == user.id,
            License.track_id == track.id,
            Order.status == OrderStatus.COMPLETED,
        )
        .first()
    )

    if not license_record:
        raise HTTPException(
            status_code=403,
            detail="You do not own this track.",
        )

    # --------------------------------------------------------------
    # R2 must be enabled.
    # --------------------------------------------------------------

    if not settings.r2_enabled:
        raise HTTPException(
            status_code=503,
            detail="Cloud storage is not configured.",
        )

    key = r2_object_key(track.audio_file_path)

    if not key:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    client = get_r2_client()

    # --------------------------------------------------------------
    # Confirm audio exists in R2.
    # --------------------------------------------------------------

    try:
        metadata = client.head_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=key,
        )
    except ClientError as exc:
        error_code = (
            exc.response
            .get("Error", {})
            .get("Code", "")
        )

        if error_code in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            raise HTTPException(
                status_code=404,
                detail="The purchased audio file is missing from storage.",
            )

        raise HTTPException(
            status_code=503,
            detail="Unable to access Cloudflare R2.",
        )

    # --------------------------------------------------------------
    # Generate private temporary download URL.
    # --------------------------------------------------------------

    params = {
        "Bucket": settings.R2_BUCKET_NAME,
        "Key": key,
    }

    content_type = metadata.get("ContentType")

    if content_type:
        params["ResponseContentType"] = content_type

    download_url = client.generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=settings.R2_DOWNLOAD_URL_EXPIRES,
    )

    # --------------------------------------------------------------
    # Send browser directly to R2.
    # --------------------------------------------------------------

    return RedirectResponse(
        url=download_url,
        status_code=307,
    )
