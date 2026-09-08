import asyncio
import unittest
from unittest.mock import patch

from app.services.growth_agent import GrowthAgentError, generate_content, generate_outreach, match_beats, run_growth_agent, scout_prospects


class TestGrowthAgentV1(unittest.TestCase):
    def test_growth_plan_never_requires_openai(self):
        from app.services import growth_agent
        with patch.object(growth_agent.settings, "OPENAI_API_KEY", "", create=True), patch.object(growth_agent.settings, "OPENAI_MODEL", "", create=True):
            result = asyncio.run(run_growth_agent({
                "totals": {"users": 10, "published_tracks": 6},
                "last_7_days": {"new_users": 10, "completed_orders": 0},
                "latest_tracks": [{"title": "Night", "genre": "Afro", "id": "t1"}],
            }))
        self.assertEqual(result["mode"], "zero_budget")
        self.assertIn("OpenAI API", result["note"])

    def test_scout_does_not_invent_or_scrape_prospects(self):
        result = asyncio.run(scout_prospects("Kenyan independent artists", "Kenya", 5))
        self.assertEqual(result["mode"], "zero_budget")
        self.assertEqual(result["prospects"], [])
        self.assertEqual(len(result["search_brief"]), 4)

    def test_scout_validates_query(self):
        with self.assertRaisesRegex(GrowthAgentError, "at least 3"):
            asyncio.run(scout_prospects("ab", "Kenya", 5))

    def test_matcher_uses_only_supplied_catalog(self):
        result = asyncio.run(match_beats(
            "dark afro 92 bpm",
            [
                {"id": "t1", "title": "Dark Night", "genre": "Afro", "bpm": 92},
                {"id": "t2", "title": "Sunny Club", "genre": "Dancehall", "bpm": 120},
            ],
            5,
        ))
        self.assertEqual(result["mode"], "zero_budget")
        self.assertEqual(result["matches"][0]["track_id"], "t1")
        self.assertNotIn("fake", [m["track_id"] for m in result["matches"]])

    def test_outreach_requires_public_prospect_context(self):
        with self.assertRaisesRegex(GrowthAgentError, "public prospect URL"):
            asyncio.run(generate_outreach({"name": "Artist"}, []))

    def test_outreach_and_content_are_local(self):
        outreach = asyncio.run(generate_outreach(
            {"name": "Artist", "public_url": "https://example.com/artist"},
            [{"title": "Night"}],
        ))
        content = asyncio.run(generate_content({"title": "Night", "genre": "Afrobeats"}))
        self.assertEqual(outreach["mode"], "zero_budget")
        self.assertIn("Night", outreach["message_short"])
        self.assertEqual(content["mode"], "zero_budget")
        self.assertEqual(len(content["hooks"]), 10)
        self.assertEqual(len(content["video_concepts"]), 10)
        self.assertEqual(len(content["captions"]), 5)


if __name__ == "__main__":
    unittest.main()
