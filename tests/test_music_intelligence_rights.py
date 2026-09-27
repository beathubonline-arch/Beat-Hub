import json
import zipfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.services.beat_finder import parse_brief, rank_tracks
from app.services.purchase_release_kit import build_purchase_release_kit, remove_release_kit
from main import app


def test_conversational_brief_parses_and_ranks_structured_metadata():
    brief = parse_brief("Dark Kenyan drill around 142 BPM under KSh 2,000 with aggressive vocals")
    assert brief.genre == "drill"
    assert brief.mood == "dark"
    assert brief.region == "kenyan"
    assert brief.bpm == 142
    assert brief.max_price == 2000
    matching = SimpleNamespace(title="Nairobi Night", description="aggressive vocals", tags="drill", instruments="808", intended_use="freestyle", similar_sound="UK drill", genre="Drill", mood="Dark", energy="high", region="Kenyan", vocal_type="open vocals", bpm=140, price=1500, created_at=None)
    wrong = SimpleNamespace(title="Sunrise", description="", tags="", instruments="guitar", intended_use="", similar_sound="", genre="Pop", mood="Happy", energy="low", region="US", vocal_type="female", bpm=95, price=1200, created_at=None)
    expensive = SimpleNamespace(**{**matching.__dict__, "title": "Too costly", "price": 5000})
    ranked = rank_tracks([wrong, expensive, matching], brief)
    assert ranked[0][0].title == "Nairobi Night"
    assert all(item[0].title != "Too costly" for item in ranked)
    assert "within budget" in ranked[0][2]


def test_find_my_beat_route_and_creator_metadata_controls_exist():
    assert "/find-my-beat" in {getattr(route, "path", "") for route in app.routes}
    source = Path("app/templates/upload_track.html").read_text(encoding="utf-8")
    for field in ("mood", "energy", "instruments", "vocal_type", "intended_use", "similar_sound", "region", "ai_training_allowed", "synthetic_likeness_allowed"):
        assert f'data-field="{field}"' in source


def test_release_kit_contains_machine_readable_frozen_rights(tmp_path, monkeypatch):
    media = tmp_path / "media"; media.mkdir()
    audio = media / "rights.wav"; audio.write_bytes(b"RIFF-rights")
    monkeypatch.setattr("app.services.purchase_release_kit.settings.MEDIA_ROOT", str(media), raising=False)
    track = SimpleNamespace(title="Rights", genre="Drill", bpm=142, audio_file_path=str(audio), cover_art_path=None, creator_profile=SimpleNamespace(stage_name="Producer"))
    order = SimpleNamespace(order_number="BH-RIGHTS-1", sales_model_at_purchase="non_exclusive", gross_amount=Decimal("1500"), currency="KES", completed_at=None)
    license_record = SimpleNamespace(id="lic-rights", granted_at=None, commercial_use_allowed=True, sampling_allowed=True, remixing_allowed=False, resale_allowed=False, ai_training_allowed=False, synthetic_likeness_allowed=False, derivatives_allowed=True, sublicensing_allowed=False)
    path = build_purchase_release_kit(license_record=license_record, order=order, track=track, buyer=SimpleNamespace(email="buyer@example.com"))
    try:
        with zipfile.ZipFile(path) as archive:
            payload = json.loads(archive.read("03_Licence/license_rights.json"))
            assert payload["license_id"] == "lic-rights"
            assert payload["rights"]["commercial_use"] is True
            assert payload["rights"]["ai_training"] is False
            assert "MACHINE-READABLE RIGHTS SNAPSHOT" in archive.read("03_Licence/BEATHUB_LICENCE_CERTIFICATE.txt").decode()
    finally:
        remove_release_kit(path)
