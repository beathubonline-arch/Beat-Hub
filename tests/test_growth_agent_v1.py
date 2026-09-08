import asyncio
import json
import unittest
from unittest.mock import patch

from app.services.growth_agent import GrowthAgentError, generate_content, generate_outreach, match_beats, run_growth_agent, scout_prospects


class TestGrowthAgentV1(unittest.TestCase):
    def _fake_client(self, payload):
        class FakeResponse:
            status_code = 200
            def json(self):
                return {"output_text": json.dumps(payload)}

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def post(self, *args, **kwargs):
                self.payload = kwargs["json"]
                return FakeResponse()

        return FakeClient()

    def test_requires_explicit_openai_configuration(self):
        from app.services import growth_agent
        with patch.object(growth_agent.settings, "OPENAI_API_KEY", "", create=True), patch.object(growth_agent.settings, "OPENAI_MODEL", "", create=True):
            with self.assertRaisesRegex(GrowthAgentError, "OPENAI_API_KEY"):
                asyncio.run(run_growth_agent({"totals": {}, "last_7_days": {}}))

    def test_growth_plan_response(self):
        from app.services import growth_agent
        client = self._fake_client({"priority": "creator referrals", "daily_targets": [10]})
        with patch.object(growth_agent.settings, "OPENAI_API_KEY", "test-key", create=True), patch.object(growth_agent.settings, "OPENAI_MODEL", "gpt-6-astra", create=True), patch.object(growth_agent.httpx, "AsyncClient", return_value=client):
            result = asyncio.run(run_growth_agent({"totals": {}, "last_7_days": {}}))
        self.assertEqual(result["priority"], "creator referrals")
        self.assertEqual(client.payload["model"], "gpt-6-astra")
        self.assertNotIn("tools", client.payload)

    def test_scout_enables_web_search_and_filters_invalid_results(self):
        from app.services import growth_agent
        prospects = [{"name": str(i), "public_url": f"https://example.com/{i}"} for i in range(20)] + [{"name": "bad", "public_url": "not-a-url"}]
        client = self._fake_client({"prospects": prospects})
        with patch.object(growth_agent.settings, "OPENAI_API_KEY", "test-key", create=True), patch.object(growth_agent.settings, "OPENAI_MODEL", "gpt-6-astra", create=True), patch.object(growth_agent.httpx, "AsyncClient", return_value=client):
            result = asyncio.run(scout_prospects("upcoming Kenyan artists looking for beats", "Kenya", 5))
        self.assertEqual(len(result["prospects"]), 5)
        self.assertEqual(client.payload["tools"], [{"type": "web_search"}])
        self.assertIn("web_search_call.action.sources", client.payload["include"])

    def test_matcher_uses_only_supplied_catalog_ids(self):
        from app.services import growth_agent
        client = self._fake_client({"matches": [{"track_id": "t1", "title": "Night"}, {"track_id": "fake", "title": "Invented"}]})
        with patch.object(growth_agent.settings, "OPENAI_API_KEY", "test-key", create=True), patch.object(growth_agent.settings, "OPENAI_MODEL", "gpt-6-astra", create=True), patch.object(growth_agent.httpx, "AsyncClient", return_value=client):
            result = asyncio.run(match_beats("dark 92 bpm afrobeat", [{"id": "t1", "title": "Night"}], 5))
        self.assertEqual([m["track_id"] for m in result["matches"]], ["t1"])

    def test_outreach_requires_public_prospect_context(self):
        from app.services import growth_agent
        with patch.object(growth_agent.settings, "OPENAI_API_KEY", "test-key", create=True), patch.object(growth_agent.settings, "OPENAI_MODEL", "gpt-6-astra", create=True):
            with self.assertRaisesRegex(GrowthAgentError, "public prospect URL"):
                asyncio.run(generate_outreach({"name": "Artist"}, []))

    def test_outreach_and_content_json(self):
        from app.services import growth_agent
        client = self._fake_client({"message_short": "Hi", "hooks": ["Hook"]})
        with patch.object(growth_agent.settings, "OPENAI_API_KEY", "test-key", create=True), patch.object(growth_agent.settings, "OPENAI_MODEL", "gpt-6-astra", create=True), patch.object(growth_agent.httpx, "AsyncClient", return_value=client):
            outreach = asyncio.run(generate_outreach({"name": "Artist", "public_url": "https://example.com/artist"}, [{"title": "Night"}]))
            content = asyncio.run(generate_content({"title": "Night", "genre": "Afrobeats"}))
        self.assertEqual(outreach["message_short"], "Hi")
        self.assertEqual(content["hooks"], ["Hook"])


if __name__ == "__main__":
    unittest.main()
