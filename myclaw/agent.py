from .memory import Memory
from .provider import LLMProvider
from .tools import TOOLS
from rich.console import Console
import json
import logging

console = Console()
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are MyClaw, a personal AI agent. "
    "You can call tools by responding ONLY with JSON: "
    '{"tool": "<name>", "args": {<key>: <value>}}. '
    "Available tools: shell(cmd), read_file(path), write_file(path, content). "
    "For all other responses, reply in plain text."
)

class Agent:
    """Personal AI agent with per-user memory and native tool calling."""

    def __init__(self, config):
        # user_id -> Memory instance for session isolation
        self._memories: dict[str, Memory] = {}
        self.provider = LLMProvider(config)
        self.model = config.get("agents", {}).get("defaults", {}).get("model", "llama3.2")

    def _get_memory(self, user_id: str) -> Memory:
        """Return (or create) a Memory instance for the given user."""
        if user_id not in self._memories:
            self._memories[user_id] = Memory(user_id=user_id)
        return self._memories[user_id]

    def close(self):
        """Close all open memory connections."""
        for mem in self._memories.values():
            mem.close()
        self._memories.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def think(self, user_message: str, user_id: str = "default") -> str:
        """Process a user message and return the agent's response."""
        mem = self._get_memory(user_id)
        mem.add("user", user_message)

        # Build proper multi-turn messages list (not a JSON-dumped string)
        history = mem.get_history()
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

        try:
            response, tool_calls = self.provider.chat(messages, self.model)
        except Exception as e:
            logger.error(f"LLM provider error: {e}")
            return f"Sorry, I encountered an error: {e}"

        # Handle native tool calls returned by Ollama
        if tool_calls:
            results = []
            for tc in tool_calls:
                tool_name = tc.get("function", {}).get("name", "")
                args = tc.get("function", {}).get("arguments", {})
                if tool_name not in TOOLS:
                    results.append(f"Unknown tool: {tool_name}")
                    continue
                try:
                    result = TOOLS[tool_name]["func"](**args)
                    mem.add("tool", f"Tool {tool_name} returned: {result}")
                    results.append(result)
                except Exception as e:
                    logger.error(f"Tool execution error: {e}")
                    results.append(f"Tool error: {e}")

            # Second LLM call with tool results
            tool_result_msg = "\n".join(results)
            followup_messages = messages + [
                {"role": "tool", "content": tool_result_msg}
            ]
            try:
                final_response, _ = self.provider.chat(followup_messages, self.model)
                mem.add("assistant", final_response)
                return final_response
            except Exception as e:
                logger.error(f"LLM second call error: {e}")
                return f"Tool executed but error getting response: {e}"

        mem.add("assistant", response)
        return response