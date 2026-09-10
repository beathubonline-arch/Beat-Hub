import asyncio

from app.services.growth_contact_v7 import _clean_email, verify_public_contact


def test_email_validation_rejects_placeholders():
    assert _clean_email("artist@example.com") is None
    assert _clean_email("hello@realartist.co.ke") == "hello@realartist.co.ke"


def test_public_instagram_is_supported_manual_dm_route(monkeypatch):
    class Response:
        status_code = 403
        text = ""
        url = "https://instagram.com/exampleartist"

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, url): return Response()

    monkeypatch.setattr("app.services.growth_contact_v7.httpx.AsyncClient", lambda **kwargs: Client())
    result = asyncio.run(verify_public_contact("https://instagram.com/exampleartist", "instagram"))
    assert result["verified"] is True
    assert result["contact_type"] == "dm"
    assert result["contact_url"] == "https://instagram.com/exampleartist"


def test_soundcloud_without_explicit_contact_is_not_send_ready(monkeypatch):
    class Response:
        status_code = 200
        text = "<html><body>artist profile, no contact details</body></html>"
        url = "https://soundcloud.com/exampleartist"

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, url): return Response()

    monkeypatch.setattr("app.services.growth_contact_v7.httpx.AsyncClient", lambda **kwargs: Client())
    result = asyncio.run(verify_public_contact("https://soundcloud.com/exampleartist", "soundcloud"))
    assert result["verified"] is False
    assert result["status"] == "contact_research"


def test_public_email_beats_profile_only(monkeypatch):
    class Response:
        status_code = 200
        text = "Bookings and collaborations: music@artist.co.ke"
        url = "https://artist.co.ke"

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, url): return Response()

    monkeypatch.setattr("app.services.growth_contact_v7.httpx.AsyncClient", lambda **kwargs: Client())
    result = asyncio.run(verify_public_contact("https://artist.co.ke", "web"))
    assert result["verified"] is True
    assert result["contact_type"] == "email"
    assert result["contact_value"] == "music@artist.co.ke"
