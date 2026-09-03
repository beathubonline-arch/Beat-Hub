from app.services.storage import _content_matches_extension


def test_valid_audio_headers_are_accepted():
    assert _content_matches_extension(b"ID3\x04\x00\x00\x00\x00\x00\x21", ".mp3")
    assert _content_matches_extension(b"\xff\xfb\x90\x64" + b"\x00" * 28, ".mp3")
    assert _content_matches_extension(b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 20, ".wav")
    assert _content_matches_extension(b"fLaC" + b"\x00" * 28, ".flac")
    assert _content_matches_extension(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 20, ".m4a")
    assert _content_matches_extension(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 20, ".m4a")


def test_mismatched_audio_headers_are_rejected():
    assert not _content_matches_extension(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 20, ".mp3")
    assert not _content_matches_extension(b"ID3\x00\x00\x00\x00" + b"\x00" * 24, ".wav")
    assert not _content_matches_extension(b"\x00" * 32, ".m4a")
