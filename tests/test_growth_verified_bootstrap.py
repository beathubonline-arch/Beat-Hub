from app.services.growth_verified_bootstrap import VERIFIED_PUBLIC_PROSPECTS


def test_verified_bootstrap_has_real_public_music_profiles():
    assert len(VERIFIED_PUBLIC_PROSPECTS) >= 5
    urls = [item["public_url"] for item in VERIFIED_PUBLIC_PROSPECTS]
    assert len(urls) == len(set(urls))
    assert all(url.startswith("https://soundcloud.com/") for url in urls)
    assert all(item["fit_score"] >= 45 for item in VERIFIED_PUBLIC_PROSPECTS)
    assert all("Kenya" in item["location"] for item in VERIFIED_PUBLIC_PROSPECTS)


def test_verified_bootstrap_never_contains_private_contact_fields():
    forbidden = {"email", "phone", "whatsapp", "password", "token"}
    for item in VERIFIED_PUBLIC_PROSPECTS:
        assert forbidden.isdisjoint(item.keys())
        assert item["prospect_type"] in {"artist", "producer"}
