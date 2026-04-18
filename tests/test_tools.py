import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
import subprocess
import httpx
import myclaw.tools


def _mock_httpx_response(status_code=200, text="", raise_error=None):
    """Create a mock httpx.AsyncClient response for use in async context managers."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text
    if raise_error:
        mock_response.raise_for_status.side_effect = raise_error
    else:
        mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _mock_httpx_error(error_class, *args, **kwargs):
    """Create a mock httpx.AsyncClient that raises an error on get()."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=error_class(*args, **kwargs))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


@pytest.fixture
def mock_workspace(tmp_path, monkeypatch):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    monkeypatch.setattr(myclaw.tools.core, "WORKSPACE", workspace)
    return workspace


def test_validate_path_valid(mock_workspace):
    path = "test.txt"
    result = myclaw.tools.validate_path(path)
    assert result == (mock_workspace / path).resolve()


def test_validate_path_traversal(mock_workspace):
    path = "../outside.txt"
    # Path traversal attacks raise "Path traversal detected"
    with pytest.raises(ValueError, match="Path traversal detected"):
        myclaw.tools.validate_path(path)


def test_shell_empty():
    assert myclaw.tools.shell("") == "Error: Empty command"
    assert myclaw.tools.shell("   ") == "Error: Empty command"


def test_shell_blocked():
    result = myclaw.tools.shell("rm -rf /")
    assert "Error: Command 'rm' is blocked for security" == result


def test_shell_not_allowed():
    result = myclaw.tools.shell("touch newfile")
    assert "Error: 'touch' not allowed." in result


def test_shell_allowed(mock_workspace):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="file1\nfile2\n", stderr="", returncode=0)
        result = myclaw.tools.shell("ls")
        assert "file1" in result
        mock_run.assert_called_once()


def test_shell_timeout():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ls", timeout=30)):
        assert "Error: Command timed out after 30 seconds" == myclaw.tools.shell("ls")


def test_read_file_success(mock_workspace):
    test_file = mock_workspace / "hello.txt"
    test_file.write_text("hello world")
    assert myclaw.tools.read_file("hello.txt") == "hello world"


def test_read_file_not_found(mock_workspace):
    result = myclaw.tools.read_file("nonexistent.txt")
    assert result.startswith("Error:")
    assert "No such file or directory" in result


def test_read_file_traversal(mock_workspace):
    result = myclaw.tools.read_file("../traversal.txt")
    assert "Error: Path traversal detected" in result


def test_write_file_success(mock_workspace):
    result = myclaw.tools.write_file("new.txt", "content")
    assert result == "File written: new.txt"
    assert (mock_workspace / "new.txt").read_text() == "content"


def test_write_file_nested(mock_workspace):
    result = myclaw.tools.write_file("subdir/deep.txt", "deep content")
    assert result == "File written: subdir/deep.txt"
    assert (mock_workspace / "subdir" / "deep.txt").read_text() == "deep content"


def test_write_file_traversal(mock_workspace):
    result = myclaw.tools.write_file("../forbidden.txt", "evil")
    assert "Error: Path traversal detected" in result


# =============================================================================
# Enhanced Browse Tool Tests
# =============================================================================

class TestBrowseErrorHandling:
    """Tests for browse tool error handling."""

    @pytest.mark.asyncio
    async def test_browse_timeout_error(self):
        """Test that timeout errors return structured guidance with Wayback suggestion."""
        mock_client = _mock_httpx_error(httpx.TimeoutException, "Connection timed out")
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await myclaw.tools.browse("https://example.com/slow-page")

            assert "Timeout" in result or "timeout" in result.lower()
            assert "web.archive.org" in result or "Wayback" in result
            assert "search_knowledge" in result.lower()
            assert "💡 Suggestions:" in result

    @pytest.mark.asyncio
    async def test_browse_connection_error(self):
        """Test that connection errors return structured guidance."""
        mock_client = _mock_httpx_error(httpx.ConnectError, "No connection")
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await myclaw.tools.browse("https://example.com/offline")

            assert "Connection" in result or "connection" in result.lower()
            assert "internet" in result.lower() or "check" in result.lower()
            assert "search_knowledge" in result.lower()

    @pytest.mark.asyncio
    async def test_browse_404_error(self):
        """Test that 404 errors return structured guidance."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=mock_response
        )
        mock_client = _mock_httpx_response(status_code=404, raise_error=mock_response.raise_for_status.side_effect)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await myclaw.tools.browse("https://example.com/deleted-page")

            assert "404" in result or "Not Found" in result
            assert "search" in result.lower() or "web search" in result.lower()
            assert "web.archive.org" in result or "Wayback" in result

    @pytest.mark.asyncio
    async def test_browse_403_error(self):
        """Test that 403 errors return structured guidance."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403 Forbidden", request=MagicMock(), response=mock_response
        )
        mock_client = _mock_httpx_response(status_code=403, raise_error=mock_response.raise_for_status.side_effect)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await myclaw.tools.browse("https://example.com/restricted")

            assert "403" in result or "Access Denied" in result or "Denied" in result
            assert "search_knowledge" in result.lower()

    @pytest.mark.asyncio
    async def test_browse_other_http_error(self):
        """Test that other HTTP errors return structured guidance."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error", request=MagicMock(), response=mock_response
        )
        mock_client = _mock_httpx_response(status_code=500, raise_error=mock_response.raise_for_status.side_effect)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await myclaw.tools.browse("https://example.com/server-error")

            assert "500" in result or "Error" in result
            assert "search_knowledge" in result.lower()

    @pytest.mark.asyncio
    async def test_browse_request_exception(self):
        """Test that generic request exceptions return structured guidance."""
        mock_client = _mock_httpx_error(httpx.RequestError, "Generic error")
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await myclaw.tools.browse("https://example.com/error")

            assert "Error" in result
            assert "search_knowledge" in result.lower()

    @pytest.mark.asyncio
    async def test_browse_unexpected_exception(self):
        """Test that unexpected exceptions return structured guidance."""
        mock_client = _mock_httpx_error(ValueError, "Unexpected")
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await myclaw.tools.browse("https://example.com/unexpected")

            assert "Unexpected" in result or "error" in result.lower()
            assert "search_knowledge" in result.lower()

    @pytest.mark.asyncio
    async def test_browse_success(self):
        """Test that successful browse returns expected format."""
        mock_client = _mock_httpx_response(
            status_code=200,
            text="<html><body>Hello World</body></html>"
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await myclaw.tools.browse("https://example.com")

            assert "URL:" in result
            assert "Status: 200" in result
            assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_browse_content_truncation(self):
        """Test that long content is truncated."""
        mock_client = _mock_httpx_response(
            status_code=200,
            text="<html><body>" + "A" * 10000 + "</body></html>"
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await myclaw.tools.browse("https://example.com", max_length=100)
            assert "truncated" in result.lower() or "Content truncated" in result


# =============================================================================
# Enhanced Search Knowledge Tests
# =============================================================================

class TestExtractSearchTerms:
    """Tests for search term extraction helper."""

    def test_extract_single_words(self):
        """Test extraction of single words."""
        terms = myclaw.tools._extract_search_terms("python programming language")
        assert "python" in terms
        assert "programming" in terms
        assert "language" in terms

    def test_extract_bigrams(self):
        """Test extraction of bigrams."""
        terms = myclaw.tools._extract_search_terms("machine learning models")
        # Should include bigrams
        has_bigram = any(" " in t for t in terms)
        assert has_bigram

    def test_extract_filters_short_words(self):
        """Test that short words are filtered out."""
        terms = myclaw.tools._extract_search_terms("a b c test")
        assert "test" in terms
        assert "a" not in terms
        assert "b" not in terms
        assert "c" not in terms

    def test_extract_removes_duplicates(self):
        """Test that duplicate terms are removed."""
        terms = myclaw.tools._extract_search_terms("test test test")
        # Should only have "test" once
        assert terms.count("test") == 1

    def test_extract_with_special_chars(self):
        """Test extraction with special characters."""
        terms = myclaw.tools._extract_search_terms("python, programming! machine-learning")
        assert "python" in terms
        assert "programming" in terms
        assert "machine" in terms or "learning" in terms


class TestSearchKnowledgeEnhancement:
    """Tests for enhanced search_knowledge empty-result handling."""

    def test_search_knowledge_no_results(self):
        """Test that empty search returns actionable guidance."""
        with patch("myclaw.tools.kb.search_notes", return_value=[]):
            result = myclaw.tools.search_knowledge("unknown query xyz", user_id="test")

            assert "No results found" in result
            assert "write_to_knowledge" in result
            assert "list_knowledge" in result
            assert "broader" in result.lower() or "different" in result.lower()
            assert "💡 Suggestions:" in result
            assert "📝 Actions you can take:" in result

    def test_search_knowledge_no_results_includes_broader_terms(self):
        """Test that empty search suggests broader terms."""
        with patch("myclaw.tools.kb.search_notes", return_value=[]):
            result = myclaw.tools.search_knowledge("machine learning python", user_id="test")

            assert "No results found" in result
            # Should suggest broader search terms
            assert "broader" in result.lower() or "Try broader" in result

    def test_search_knowledge_with_results(self):
        """Test that search with results returns formatted list."""
        mock_note = MagicMock()
        mock_note.title = "Test Note"
        mock_note.permalink = "test-note"
        mock_note.observations = []
        mock_note.tags = ["test"]

        with patch("myclaw.tools.kb.search_notes", return_value=[mock_note]):
            result = myclaw.tools.search_knowledge("test", user_id="test")

            assert "Search results" in result
            assert "Test Note" in result
            assert "write_to_knowledge" not in result  # Should not appear when results exist

    def test_search_knowledge_with_observations(self):
        """Test that search results include observations."""
        mock_obs = MagicMock()
        mock_obs.category = "info"
        mock_obs.content = "This is a test observation"

        mock_note = MagicMock()
        mock_note.title = "Test Note"
        mock_note.permalink = "test-note"
        mock_note.observations = [mock_obs]
        mock_note.tags = []

        with patch("myclaw.tools.kb.search_notes", return_value=[mock_note]):
            result = myclaw.tools.search_knowledge("test", user_id="test")

            assert "Test Note" in result
            assert "[info]" in result or "This is a test" in result

    def test_search_knowledge_error_handling(self):
        """Test that search handles errors gracefully."""
        with patch("myclaw.tools.kb.search_notes", side_effect=Exception("DB Error")):
            result = myclaw.tools.search_knowledge("test", user_id="test")

            assert "Error" in result
            assert "searching knowledge" in result.lower() or "Error searching" in result


# =============================================================================
# Backward Compatibility Tests
# =============================================================================

class TestBackwardCompatibility:
    """Tests to ensure backward compatibility."""

    def test_search_knowledge_still_contains_original_phrase(self):
        """Test that empty results still contain 'No results found' phrase."""
        with patch("myclaw.tools.kb.search_notes", return_value=[]):
            result = myclaw.tools.search_knowledge("test query", user_id="test")

            # Code checking for "No results found" should still work
            assert "No results found" in result

    @pytest.mark.asyncio
    async def test_browse_error_still_contains_error_prefix(self):
        """Test that errors still contain 'Error' prefix."""
        mock_client = _mock_httpx_error(httpx.TimeoutException, "Connection timed out")
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await myclaw.tools.browse("https://example.com")

            # Code checking for "Error" prefix should still work
            assert "Error" in result or "error" in result.lower()
