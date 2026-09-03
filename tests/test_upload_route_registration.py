from main import app


def test_dashboard_upload_routes_use_canonical_publisher():
    upload_routes = [
        route
        for route in app.routes
        if getattr(route, "path", "") == "/dashboard/upload"
    ]

    assert upload_routes, "dashboard upload route is not registered"
    assert upload_routes[0].endpoint.__module__ == "app.routers.music_publish"
    assert upload_routes[0].endpoint.__name__ == "publish_tracks"


def test_dashboard_upload_has_both_get_and_post_routes():
    upload_routes = [
        route
        for route in app.routes
        if getattr(route, "path", "") == "/dashboard/upload"
    ]

    methods = set()
    for route in upload_routes:
        methods.update(getattr(route, "methods", set()))

    assert "GET" in methods
    assert "POST" in methods
