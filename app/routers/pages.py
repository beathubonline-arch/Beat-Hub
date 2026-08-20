@router.get("/beats")
def beats(
    request: Request,
    current_user: Optional[User] = Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "beats.html",
        ctx(request, current_user),
    )


@router.get("/sessions")
def sessions(
    request: Request,
    current_user: Optional[User] = Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "sessions.html",
        ctx(request, current_user),
    )


@router.get("/hot-picks")
def hot_picks(
    request: Request,
    current_user: Optional[User] = Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "hot-picks.html",
        ctx(request, current_user),
    )
