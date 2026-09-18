from types import SimpleNamespace

from app.routers.api_v1 import _track_payload


def test_track_payload_exposes_honest_account_verification_state():
    profile = SimpleNamespace(
        stage_name="Verified Producer",
        slug="verified-producer",
        user=SimpleNamespace(is_verified=True),
    )
    track = SimpleNamespace(
        id="track-1",
        title="Pulse Test",
        slug="pulse-test",
        description=None,
        genre="Afrobeat",
        bpm=108,
        price=1500,
        currency="KES",
        sales_model="non_exclusive",
        is_sold=False,
        is_published=True,
        creator_profile=profile,
    )

    payload = _track_payload(track)

    assert payload["producer_verified"] is True
    assert payload["producer"] == "Verified Producer"
    assert payload["track_url"].endswith("/track/pulse-test")


def test_track_payload_defaults_unverified_when_profile_user_is_missing():
    profile = SimpleNamespace(stage_name="Legacy Producer", slug="legacy-producer")
    track = SimpleNamespace(
        id="track-2",
        title="Legacy Beat",
        slug="legacy-beat",
        description=None,
        genre=None,
        bpm=None,
        price=500,
        currency="KES",
        sales_model="non_exclusive",
        is_sold=False,
        is_published=True,
        creator_profile=profile,
    )

    assert _track_payload(track)["producer_verified"] is False

