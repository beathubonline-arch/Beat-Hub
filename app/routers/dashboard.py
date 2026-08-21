store_url = (
    f"/store/{profile.slug}"
    if getattr(profile, "slug", None)
    else None
)
