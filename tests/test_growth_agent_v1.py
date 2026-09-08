import asyncio
import json
import unittest
from unittest.mock import patch

from app.services.growth_agent import GrowthAgentError, run_growth_agent


class TestGrowthAgentV1(unittest.TestCase):
    def test_requires_explicit_openai_configuration(self):
        from app.services import growth_agent

        with patch.object(growth_agent.settings, "OPENAI_API_KEY", "", create=True), patch.object(growth_agent.settings, "OPENAI_MODEL", "", create=True):
            with self.assertRaisesRegex(GrowthAgentError, "OPENAI_API_KEY"):
                asyncio.run(run_growth_agent({"totals": {}, "last_7_days": {}}))

    def test_parses_responses_api_json(self):
        from app.services import growth_agent

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"output_text": json.dumps({"priority": "creator referrals", "daily_targets": [10]})}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                self.assert_url = args[0]
                self.assert_model = kwargs["json"]["model"]
                return FakeResponse()

        fake_client = FakeClient()
        with patch.object(growth_agent.settings, "OPENAI_API_KEY", "test-key", create=True), patch.object(growth_agent.settings, "OPENAI_MODEL", "test-model", create=True), patch.object(growth_agent.httpx, "AsyncClient", return_value=fake_client):
            result = asyncio.run(run_growth_agent({"totals": {}, "last_7_days": {}}))

        self.assertEqual(result["priority"], "creator referrals")
        self.assertEqual(result["daily_targets"], [10])
        self.assertEqual(fake_client.assert_url, "https://api.openai.com/v1/responses")
        self.assertEqual(fake_client.assert_model, "test-model")


if __name__ == "__main__":
    unittest.main()
