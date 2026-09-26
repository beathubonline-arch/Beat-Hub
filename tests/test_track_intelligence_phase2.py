from types import SimpleNamespace
from app.services.track_intelligence import analyse_track, campaign_angle


def test_track_intelligence_uses_existing_metadata_without_local_audio():
    track = SimpleNamespace(bpm=104, genre="Afro-fusion", audio_file_path="/missing/audio.mp3")
    data = analyse_track(track)
    assert data["bpm"] == 104
    assert data["tempo"] == "mid-tempo"
    assert data["genre"] == "Afro-fusion"
    assert data["source"] == "metadata"
    assert "104 BPM" in campaign_angle(data)


def test_track_intelligence_never_requires_audio_for_campaign():
    track = SimpleNamespace(bpm=None, genre=None, audio_file_path=None)
    data = analyse_track(track)
    assert data["source"] == "metadata"
    assert data["energy"] == "unknown"
    assert campaign_angle(data)
