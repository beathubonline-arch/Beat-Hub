import asyncio

from app.routers import ai_cover_studio
from app.services import higgsfield
from main import app


def test_cover_studio_routes_are_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/dashboard/ai-cover-studio" in paths
    assert "/dashboard/ai-cover-studio/generate" in paths
    assert "/dashboard/ai-cover-studio/apply" in paths


def test_apply_token_is_signed_and_scoped(monkeypatch):
    monkeypatch.setattr(ai_cover_studio.settings, "SESSION_SECRET", "test-secret")
    token = ai_cover_studio._serializer().dumps({
        "profile_id": "profile-1", "track_id": "track-1", "storage_path": "ai-covers/example.png"
    })
    payload = ai_cover_studio._serializer().loads(token, max_age=60)
    assert payload["profile_id"] == "profile-1"
    assert payload["track_id"] == "track-1"


def test_generation_polls_downloads_and_stores(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"image-data"

    class FakeResponse:
        def __init__(self, status_code, payload=None, body=b"", content_type="application/json"):
            self.status_code = status_code
            self._payload = payload
            self.content = body
            self.headers = {"content-type": content_type}

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, url, **_kwargs):
            assert url.endswith("/marketing-studio/image")
            return FakeResponse(200, {"status": "queued", "request_id": "request-1", "status_url": "https://api.higgsfield.ai/requests/request-1/status"})
        async def get(self, url, **_kwargs):
            if url.endswith("/status"):
                return FakeResponse(200, {"status": "completed", "request_id": "request-1", "images": [{"url": "https://cdn.example.com/cover.png"}]})
            return FakeResponse(200, body=png, content_type="image/png")

    async def fake_save(upload, subfolder, allowed):
        assert subfolder == "ai-covers"
        assert ".png" in allowed
        assert upload.file.read() == png
        return "ai-covers/cover.png"

    async def no_sleep(_seconds): return None

    monkeypatch.setattr(higgsfield.settings, "HIGGSFIELD_API_KEY_ID", "key")
    monkeypatch.setattr(higgsfield.settings, "HIGGSFIELD_API_KEY_SECRET", "secret")
    monkeypatch.setattr(higgsfield.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(higgsfield, "save_upload", fake_save)
    monkeypatch.setattr(higgsfield.asyncio, "sleep", no_sleep)

    result = asyncio.run(higgsfield.generate_and_store_cover("A detailed neon Nairobi cover concept"))
    assert result.request_id == "request-1"
    assert result.storage_path == "ai-covers/cover.png"
    assert result.preview_url == "/media/ai-covers/cover.png"


def test_status_url_rejects_other_hosts():
    try:
        higgsfield._safe_status_url("https://attacker.example/status")
    except higgsfield.HiggsfieldError:
        pass
    else:
        raise AssertionError("Untrusted status host was accepted")


def test_combined_console_key_is_supported(monkeypatch):
    monkeypatch.setattr(higgsfield.settings, "HIGGSFIELD_API_KEY", "key-id:key-secret")
    monkeypatch.setattr(higgsfield.settings, "HIGGSFIELD_API_KEY_ID", "")
    monkeypatch.setattr(higgsfield.settings, "HIGGSFIELD_API_KEY_SECRET", "")
    assert higgsfield.is_configured() is True
    assert higgsfield._headers()["Authorization"] == "Key key-id:key-secret"
