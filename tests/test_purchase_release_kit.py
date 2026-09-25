import csv
import io
import zipfile
from decimal import Decimal
from types import SimpleNamespace

from app.services.purchase_release_kit import build_purchase_release_kit, remove_release_kit
from main import app


def test_release_kit_route_is_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/account/release-kit/{license_id}" in paths


def test_completed_purchase_release_kit_contains_delivery_files(tmp_path, monkeypatch):
    media = tmp_path / "media"
    audio = media / "audio" / "eldoret-nights.wav"
    cover = media / "covers" / "eldoret-nights.jpg"
    audio.parent.mkdir(parents=True)
    cover.parent.mkdir(parents=True)
    audio.write_bytes(b"RIFF-test-audio")
    cover.write_bytes(b"jpeg-test-cover")
    monkeypatch.setattr("app.services.purchase_release_kit.settings.MEDIA_ROOT", str(media), raising=False)

    track = SimpleNamespace(
        title="Eldoret Nights",
        genre="Afrobeats",
        bpm=104,
        audio_file_path=str(audio),
        cover_art_path=str(cover),
        creator_profile=SimpleNamespace(stage_name="Producer One"),
    )
    order = SimpleNamespace(
        order_number="BH-RELEASE-001",
        sales_model_at_purchase="exclusive",
        gross_amount=Decimal("5000.00"),
        currency="KES",
        completed_at=None,
    )
    license_record = SimpleNamespace(id="lic-001", granted_at=None)
    buyer = SimpleNamespace(email="artist@example.com")

    archive_path = build_purchase_release_kit(
        license_record=license_record,
        order=order,
        track=track,
        buyer=buyer,
    )
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            assert "01_Audio/Eldoret Nights.wav" in names
            assert "02_Artwork/Eldoret Nights_Cover.jpg" in names
            assert "03_Licence/BEATHUB_LICENCE_CERTIFICATE.txt" in names
            assert "04_Metadata/RELEASE_CREDITS.txt" in names
            assert "04_Metadata/release_metadata.csv" in names
            assert "04_Metadata/release_metadata.json" in names
            certificate = archive.read("03_Licence/BEATHUB_LICENCE_CERTIFICATE.txt").decode()
            assert "BH-RELEASE-001" in certificate
            assert "Exclusive" in certificate
            assert "artist@example.com" in certificate
            metadata = list(csv.DictReader(io.StringIO(archive.read("04_Metadata/release_metadata.csv").decode())))
            assert metadata[0]["title"] == "Eldoret Nights"
            assert metadata[0]["producer"] == "Producer One"
    finally:
        remove_release_kit(archive_path)


def test_purchase_page_exposes_release_kit_action():
    source = open("app/templates/account_purchases.html", encoding="utf-8").read()
    assert "/account/release-kit/{{ license.id }}" in source
    assert "Download Release Kit" in source
