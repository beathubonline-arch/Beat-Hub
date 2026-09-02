import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import storage


class FakeFile:
    def __init__(self, data: bytes):
        self.data = data
        self.position = 0

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        if whence == 0:
            self.position = offset
        elif whence == 1:
            self.position += offset
        elif whence == 2:
            self.position = len(self.data) + offset
        return self.position

    def read(self, size=-1):
        if size < 0:
            size = len(self.data) - self.position
        chunk = self.data[self.position:self.position + size]
        self.position += len(chunk)
        return chunk


def upload_file(data=b"ID3" + b"x" * 100, filename="beat.mp3"):
    return SimpleNamespace(filename=filename, file=FakeFile(data))


def test_stream_size_does_not_consume_upload():
    file = upload_file()
    assert storage._stream_size(file) == len(file.file.data)
    assert file.file.tell() == 0


def test_mp3_magic_bytes_are_accepted():
    file = upload_file()
    assert storage._validate(file, {".mp3"}) == ".mp3"


def test_wrong_audio_content_is_rejected():
    file = upload_file(data=b"not-an-mp3", filename="beat.mp3")
    with pytest.raises(storage.UploadValidationError):
        storage._validate(file, {".mp3"})


def test_r2_upload_uses_worker_thread(monkeypatch):
    calls = []

    class FakeClient:
        def upload_fileobj(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(storage, "_r2_client", lambda: FakeClient())
    monkeypatch.setattr(storage, "_r2_bucket", lambda: "beathub")
    monkeypatch.setattr(storage.settings, "MAX_UPLOAD_MB", 10)

    result = asyncio.run(
        storage.save_upload_to_r2(
            upload_file(),
            "audio",
            {".mp3"},
        )
    )

    assert result.startswith("r2://beathub/audio/")
    assert len(calls) == 1
    assert calls[0][1]["ExtraArgs"]["ContentType"] == "audio/mpeg"


def test_upload_page_has_real_progress_and_duplicate_submit_protection():
    html = Path("app/templates/upload_track.html").read_text(encoding="utf-8")
    assert "xhr.upload.addEventListener('progress'" in html
    assert "event.preventDefault();" in html
    assert "let submitting=false" in html
    assert "publish.disabled=true" in html
    assert "Upload received — processing…" in html
