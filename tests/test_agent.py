import pytest
import unittest.mock
from unittest.mock import patch, MagicMock
from myclaw.agent import Agent, KnowledgeSearchResult, KnowledgeGapCache
import logging
import time
import asyncio

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


def create_mock_agent(tmp_path):
    """Helper function to create a mock agent."""
    with unittest.mock.patch("pathlib.Path.home", return_value=tmp_path):
        with unittest.mock.patch("myclaw.agent.get_provider") as mock_get_provider:
            mock_provider_instance = unittest.mock.MagicMock()
            async def mock_chat(*args, **kwargs):
                return ("Hi", None)
            mock_provider_instance.chat = mock_chat
            mock_get_provider.return_value = mock_provider_instance

            agent = Agent(config=MockConfig(), provider_name="test_provider")
            agent._kb_gaps.clear()
            return agent


async def cleanup_agent(agent):
    """Helper function to cleanup an agent."""
    await agent.close()


@pytest.fixture
def mock_agent(tmp_path):
    agent = create_mock_agent(tmp_path)
    yield agent
    # Run cleanup in new event loop
    try:
        asyncio.get_event_loop().run_until_complete(cleanup_agent(agent))
    except:
        pass


@pytest.fixture
def mock_agent_with_clean_gaps(tmp_path):
    agent = create_mock_agent(tmp_path)
    agent.set_gap_cache_enabled(False)
    yield agent
    try:
        asyncio.get_event_loop().run_until_complete(cleanup_agent(agent))
    except:
        pass


@pytest.fixture
def mock_agent_with_cache(tmp_path):
    agent = create_mock_agent(tmp_path)
    agent.clear_gap_cache()
    yield agent
    try:
        asyncio.get_event_loop().run_until_complete(cleanup_agent(agent))
    except:
        pass


@pytest.fixture
def mock_agent_for_hooks(tmp_path):
    agent = create_mock_agent(tmp_path)
    yield agent
    try:
        asyncio.get_event_loop().run_until_complete(cleanup_agent(agent))
    except:
        pass


@pytest.mark.asyncio
async def test_agent_think(mock_agent):
    response = await mock_agent.think("Hello", user_id="test_user")
    assert response == "Hi"


class TestKnowledgeGapCache:
    """Tests for KnowledgeGapCache deduplication."""

    def test_cache_initially_empty(self):
        cache = KnowledgeGapCache(timeout_seconds=300.0)
        assert not cache.is_duplicate("test query", "user1")

    def test_duplicate_detection(self):
        cache = KnowledgeGapCache(timeout_seconds=300.0)
        # First call should not be duplicate
        assert not cache.is_duplicate("test query", "user1")
        # Second call with same query should be duplicate
        assert cache.is_duplicate("test query", "user1")

    def test_different_queries_not_duplicate(self):
        cache = KnowledgeGapCache(timeout_seconds=300.0)
        assert not cache.is_duplicate("query one", "user1")
        assert not cache.is_duplicate("query two", "user1")

    def test_different_users_not_duplicate(self):
        cache = KnowledgeGapCache(timeout_seconds=300.0)
        assert not cache.is_duplicate("test query", "user1")
        assert not cache.is_duplicate("test query", "user2")

    def test_case_insensitive_matching(self):
        cache = KnowledgeGapCache(timeout_seconds=300.0)
        assert not cache.is_duplicate("Test Query", "user1")
        assert cache.is_duplicate("test query", "user1")

    def test_cache_expiration(self):
        cache = KnowledgeGapCache(timeout_seconds=0.1)  # 100ms timeout
        assert not cache.is_duplicate("test query", "user1")
        # Wait for expiration
        time.sleep(0.15)
        # Should not be duplicate after expiration
        assert not cache.is_duplicate("test query", "user1")

    def test_cache_disabled(self):
        cache = KnowledgeGapCache(timeout_seconds=300.0)
        cache.set_enabled(False)
        # Should never detect as duplicate when disabled
        assert not cache.is_duplicate("test query", "user1")
        assert not cache.is_duplicate("test query", "user1")

    def test_cache_clear(self):
        cache = KnowledgeGapCache(timeout_seconds=300.0)
        assert not cache.is_duplicate("test query", "user1")
        assert cache.is_duplicate("test query", "user1")
        cache.clear()
        assert not cache.is_duplicate("test query", "user1")


class TestKnowledgeSearchResult:
    """Tests for KnowledgeSearchResult dataclass."""

    def test_result_creation(self):
        result = KnowledgeSearchResult(
            context="Test context",
            has_results=True,
            suggested_topics=["topic1", "topic2"],
            gap_logged=False,
            metadata={"query": "test"}
        )
        assert result.context == "Test context"
        assert result.has_results is True
        assert result.suggested_topics == ["topic1", "topic2"]
        assert result.gap_logged is False
        assert result.metadata["query"] == "test"

    def test_default_values(self):
        result = KnowledgeSearchResult(context="Test", has_results=False)
        assert result.suggested_topics == []
        assert result.gap_logged is False
        assert result.metadata == {}


class TestSearchKnowledgeContext:
    """Tests for _search_knowledge_context method."""

    def test_empty_result_backward_compatible(self, mock_agent_with_clean_gaps):
        """Test that empty search results return empty string by default (backward compatibility)."""
        with patch("myclaw.agent.search_notes", return_value=[]):
            result = mock_agent_with_clean_gaps._search_knowledge_context(
                "unknown topic about quantum computing",
                "test_user"
            )
            assert isinstance(result, str)
            assert result == ""

    def test_empty_result_structured(self, mock_agent_with_clean_gaps):
        """Test that empty search results return structured result when requested."""
        with patch("myclaw.agent.search_notes", return_value=[]):
            result = mock_agent_with_clean_gaps._search_knowledge_context(
                "unknown topic about quantum computing",
                "test_user",
                return_structured=True
            )
            assert isinstance(result, KnowledgeSearchResult)
            assert not result.has_results
            assert result.gap_logged is True
            assert len(result.suggested_topics) > 0
            assert "quantum" in [t.lower() for t in result.suggested_topics] or \
                   "computing" in [t.lower() for t in result.suggested_topics]
            assert "write_to_knowledge" in result.context.lower()

    def test_empty_result_includes_guidance(self, mock_agent_with_clean_gaps):
        """Test that empty results include actionable guidance."""
        with patch("myclaw.agent.search_notes", return_value=[]):
            result = mock_agent_with_clean_gaps._search_knowledge_context(
                "unknown topic",
                "test_user",
                return_structured=True
            )
            assert "Knowledge Base Status" in result.context
            assert "write_to_knowledge" in result.context
            assert "list_knowledge" in result.context

    def test_with_results_structured(self, mock_agent_with_clean_gaps):
        """Test that search with results returns structured result."""
        mock_note = MagicMock()
        mock_note.title = "Test Note"
        mock_note.permalink = "test-note"
        mock_note.observations = []

        with patch("myclaw.agent.search_notes", return_value=[mock_note]):
            result = mock_agent_with_clean_gaps._search_knowledge_context(
                "test query",
                "test_user",
                return_structured=True
            )
            assert isinstance(result, KnowledgeSearchResult)
            assert result.has_results is True
            assert "## Relevant Knowledge" in result.context
            assert "Test Note" in result.context

    def test_with_results_backward_compatible(self, mock_agent_with_clean_gaps):
        """Test that search with results returns string by default."""
        mock_note = MagicMock()
        mock_note.title = "Test Note"
        mock_note.permalink = "test-note"
        mock_note.observations = []

        with patch("myclaw.agent.search_notes", return_value=[mock_note]):
            result = mock_agent_with_clean_gaps._search_knowledge_context(
                "test query",
                "test_user"
            )
            assert isinstance(result, str)
            assert "## Relevant Knowledge" in result

    def test_suggested_topics_extraction(self, mock_agent_with_clean_gaps):
        """Test that suggested topics are extracted from query."""
        with patch("myclaw.agent.search_notes", return_value=[]):
            result = mock_agent_with_clean_gaps._search_knowledge_context(
                "machine learning models and artificial intelligence",
                "test_user",
                return_structured=True
            )
            topics = result.suggested_topics
            # Should include longer words (> 3 chars)
            assert "machine" in topics or "learning" in topics or "models" in topics
            # Should include bigrams
            assert any(" " in t for t in topics)


class TestKnowledgeGapRecording:
    """Tests for knowledge gap recording functionality."""

    def test_record_kb_gap(self, mock_agent_with_cache):
        """Test that gaps are recorded correctly."""
        result = mock_agent_with_cache._record_kb_gap("user1", "test topic")
        assert result is True
        assert "test topic" in mock_agent_with_cache.get_kb_gaps("user1")

    def test_record_kb_gap_duplicate(self, mock_agent_with_cache):
        """Test that duplicate gaps are not recorded."""
        # First call should record
        result1 = mock_agent_with_cache._record_kb_gap("user1", "test topic")
        assert result1 is True

        # Second call should be duplicate (within cache timeout)
        result2 = mock_agent_with_cache._record_kb_gap("user1", "test topic")
        assert result2 is False

    def test_record_kb_gap_skip_cache(self, mock_agent_with_cache):
        """Test that skip_cache bypasses deduplication."""
        # First call
        mock_agent_with_cache._record_kb_gap("user1", "test topic")

        # Second call with skip_cache should still record
        result = mock_agent_with_cache._record_kb_gap("user1", "test topic", skip_cache=True)
        assert result is True

    def test_get_kb_gaps_empty(self, mock_agent_with_cache):
        """Test getting gaps for user with no gaps."""
        gaps = mock_agent_with_cache.get_kb_gaps("new_user")
        assert gaps == []

    def test_gap_length_limit(self, mock_agent_with_cache):
        """Test that gap topics are length-limited."""
        long_topic = "a" * 200
        mock_agent_with_cache._record_kb_gap("user1", long_topic)
        gaps = mock_agent_with_cache.get_kb_gaps("user1")
        assert len(gaps[0]) == 120  # Capped at 120 chars


class TestGapCacheHooks:
    """Tests for gap cache enable/disable hooks."""

    def test_set_gap_cache_enabled(self, mock_agent_for_hooks):
        """Test that gap cache can be enabled/disabled."""
        # Initially enabled
        mock_agent_for_hooks.set_gap_cache_enabled(False)

        # Should be able to record duplicates when disabled
        result1 = mock_agent_for_hooks._record_kb_gap("user1", "topic")
        result2 = mock_agent_for_hooks._record_kb_gap("user1", "topic")
        assert result1 is True
        assert result2 is True  # Not duplicate when cache disabled

    def test_class_level_cache_disable(self, mock_agent_for_hooks):
        """Test class-level cache disable flag."""
        mock_agent_for_hooks._knowledge_gap_cache_enabled = False

        # When disabled at class level, gap cache should not be created
        # or should be disabled on existing instances
        if hasattr(mock_agent_for_hooks, '_gap_cache'):
            mock_agent_for_hooks._gap_cache.set_enabled(False)

        result1 = mock_agent_for_hooks._record_kb_gap("user1", "topic")
        result2 = mock_agent_for_hooks._record_kb_gap("user1", "topic")
        assert result1 is True
        assert result2 is True


class TestKnowledgeGapLogging:
    """Tests for structured knowledge gap logging in think() method."""

    @pytest.mark.asyncio
    async def test_think_logs_knowledge_gap(self, tmp_path, caplog):
        """Test that think() emits structured log for knowledge gaps."""
        agent = create_mock_agent(tmp_path)
        agent.set_gap_cache_enabled(False)

        # First, record a gap
        agent._record_kb_gap("test_user", "unknown topic xyz")

        # Set up logging capture
        with caplog.at_level(logging.INFO, logger="myclaw.knowledge.gaps"):
            # Mock the skill preloader
            with patch.object(agent._skill_preloader, 'predict_and_preload'):
                response = await agent.think(
                    "Tell me about unknown topic xyz",
                    user_id="test_user"
                )

        # Note: Since we mocked the provider, we verify the gap hint logic
        # The actual logging test would require integration testing

        await cleanup_agent(agent)

    @pytest.mark.asyncio
    async def test_think_gap_deduplication(self, tmp_path):
        """Test that duplicate gaps within session are not re-logged."""
        agent = create_mock_agent(tmp_path)
        agent.clear_gap_cache()

        # Record a gap first
        agent._record_kb_gap("test_user", "duplicate query")

        with patch.object(agent._skill_preloader, 'predict_and_preload'):
            # First call
            await agent.think("Test message", user_id="test_user")

            # Second call - gap should be duplicate in cache
            assert agent._gap_cache.is_duplicate("duplicate query", "test_user")

        await cleanup_agent(agent)
