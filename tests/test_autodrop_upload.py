from pathlib import Path

TEMPLATE = Path("app/templates/upload_track.html").read_text(encoding="utf-8")


def test_autodrop_is_integrated_into_creator_upload():
    assert "⚡ AutoDrop" in TEMPLATE
    assert "prepareFiles(picker.files)" in TEMPLATE
    assert "/dashboard/analyze-bpm" in TEMPLATE
    assert "/dashboard/upload/sign" in TEMPLATE
    assert "localStorage.setItem('beathub_autodrop'" in TEMPLATE


def test_autodrop_keeps_review_and_canonical_publish_path():
    assert "Review &amp; Publish" in TEMPLATE
    assert "content_type" in TEMPLATE
    assert "sales_model" in TEMPLATE
    assert "audio_r2_path" in TEMPLATE
    assert "cover_r2_path" in TEMPLATE
    assert "fetch('/dashboard/upload'" in TEMPLATE
