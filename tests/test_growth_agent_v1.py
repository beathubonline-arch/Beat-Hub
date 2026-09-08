import json

import pytest

from app.services.growth_agent import GrowthAgentError, run_growth_agent


@pytest.mark.asyncio
async def test_growth_agent_requires_explicit_openai_configuration(monkeypatch):
    from app.services import growth_agent

    monkeypatch.setattr(growth_agent.settings, "OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(growth_agent.settings, "OPENAI_MODEL", "", raising=False)

    with pytest.raises(GrowthAgentError, match="OPENAI_API_KEY"):
        await run_growth_agent({"totals": {}, "last_7_days": {}})


@pytest.mark.asyncio
async def test_growth_agent_parses_responses_api_json(monkeypatch):
    from app.services import growth_agent

    monkeypatch.setattr(growth_agent.settings, "OPENAI_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(growth_agent.settings, "OPENAI_MODEL", "test-model", raising=False)

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
            assert args[0] == "https://api.openai.com/v1/responses"
            assert kwargs["headers"]["Authorization"] == "Bearer test-key"
            assert kwargs["json"]["model"] == "test-model"
            assert kwargs["json"]["store"] is False
            return FakeResponse()

    monkeypatch.setattr(growth_agent.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    result = await run_growth_agent({"totals": {}, "last_7_days": {}})
    assert result["priority"] == "creator referrals"
    assert result["daily_targets"] == [10]
