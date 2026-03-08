import pytest
import unittest.mock
from myclaw.agent import Agent

# Mock config
class MockConfig:
    class AgentsConfig:
        class DefaultsConfig:
            provider = "test_provider"
            model = "test_model"
        defaults = DefaultsConfig()
        named = []
    agents = AgentsConfig()

    class ProvidersConfig:
        pass
    providers = ProvidersConfig()

@pytest.fixture
def mock_agent(tmp_path):
    with unittest.mock.patch("pathlib.Path.home", return_value=tmp_path):
        with unittest.mock.patch("myclaw.agent.get_provider") as mock_get_provider:
            mock_provider_instance = unittest.mock.MagicMock()
            async def mock_chat(*args, **kwargs):
                return ("Hi", None)
            mock_provider_instance.chat = mock_chat
            mock_get_provider.return_value = mock_provider_instance

            agent = Agent(config=MockConfig(), provider_name="test_provider")
            yield agent
            agent.close()

@pytest.mark.asyncio
async def test_agent_think(mock_agent):
    response = await mock_agent.think("Hello", user_id="test_user")
    assert response == "Hi"
    # mock_agent.provider.chat.assert_called()
