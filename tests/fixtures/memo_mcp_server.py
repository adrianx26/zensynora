"""A tiny stdio MCP server with memo tools (stands in for a memopad-like server in tests)."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("memo")
_MEMOS: dict[str, str] = {}


@mcp.tool()
def save_memo(title: str, text: str) -> str:
    """Save a memo."""
    _MEMOS[title] = text
    return f"saved {title}"


@mcp.tool()
def find_memo(query: str, limit: int = 5) -> str:
    """Find memos containing the query."""
    hits = [f"{t}: {x}" for t, x in _MEMOS.items() if query.lower() in (t + x).lower()][:limit]
    return "\n".join(hits) or "no memos"


if __name__ == "__main__":
    mcp.run()
