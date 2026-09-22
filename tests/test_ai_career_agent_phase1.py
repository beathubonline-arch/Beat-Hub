from types import SimpleNamespace

from app.services.release_kit import build_release_kit
from main import app


def test_ai_career_agent_routes_are_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/dashboard/ai-career-agent" in paths
    assert "/dashboard/ai-career-agent/create" in paths


def test_release_kit_has_complete_phase_one_sections():
    track = SimpleNamespace(title="Eldoret Nights", genre="Afrobeats")
    kit = build_release_kit(track, audience="East African Gen Z", goal="drive saves")
    assert "Eldoret Nights" in kit["positioning"]
    assert len(kit["hooks"]) == 5
    assert len(kit["captions"]) >= 3
    assert len(kit["video_ideas"]) >= 3
    assert len(kit["rollout"]) == 7
    assert set(kit["promo_copy"]) == {"instagram", "tiktok", "youtube"}
    assert len(kit["checklist"]) >= 8
