from app.routers.music_publish import _direct_paths


def test_direct_paths_splits_multi_upload_payload():
    class Form:
        def getlist(self, name):
            return ["r2://beathub/audio/a.mp3\nr2://beathub/audio/b.mp3"] if name == "audio_r2_paths" else []

    assert _direct_paths(Form(), "audio_r2_paths") == [
        "r2://beathub/audio/a.mp3",
        "r2://beathub/audio/b.mp3",
    ]


def test_upload_template_uses_direct_r2_and_json_finalize_with_fallback():
    from pathlib import Path

    html = Path("app/templates/upload_track.html").read_text(encoding="utf-8")
    assert "/dashboard/upload/sign" in html
    assert "/dashboard/upload/blob?kind=" in html
    assert "PUT" in html
    assert "FormData" in html
    assert "Content-Type':'application/json'" in html or 'Content-Type":"application/json"' in html
    assert "JSON.stringify({items:payload})" in html
