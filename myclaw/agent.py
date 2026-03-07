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
    "Available tools: shell(cmd), read_file(path), write_file(path, content), "
    "delegate(agent_name, task), list_tools(), register_tool(name, code), "
    "schedule(task, delay, every, user_id), cancel_schedule(job_id), list_schedules(). "
    "For all other responses, reply in plain text."
)


class Agent:
    """Personal AI agent with per-user memory, native tool calling, multi-agent delegation."""

    def __init__(self, config, model: str = None, system_prompt: str = None):
        self._memories: dict[str, Memory] = {}
        self.provider = LLMProvider(config)
        # Allow model override (for named agents); fall back to config default
        try:
            cfg_model = config.agents.defaults.model
        except Exception:
            cfg_model = "llama3.2"
        self.model = model or cfg_model
        self.system_prompt = system_prompt or SYSTEM_PROMPT

    def _get_memory(self, user_id: str) -> Memory:
        if user_id not in self._memories:
            self._memories[user_id] = Memory(user_id=user_id)
        return self._memories[user_id]

    def close(self):
        for mem in self._memories.values():
            mem.close()
        self._memories.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def think(self, user_message: str, user_id: str = "default", _depth: int = 0) -> str:
        """Process a user message and return the agent's response.

        _depth tracks sub-agent delegation depth — prevents infinite loops.
        """
        mem = self._get_memory(user_id)
        mem.add("user", user_message)

        history = mem.get_history()
        messages = [{"role": "system", "content": self.system_prompt}] + history

        try:
            response, tool_calls = self.provider.chat(messages, self.model)
        except Exception as e:
            logger.error(f"LLM provider error: {e}")
            return f"Sorry, I encountered an error: {e}"

        if tool_calls:
            results = []
            for tc in tool_calls:
                tool_name = tc.get("function", {}).get("name", "")
                args = tc.get("function", {}).get("arguments", {})

                if tool_name not in TOOLS:
                    results.append(f"Unknown tool: {tool_name}")
                    continue

                # Inject delegation depth so delegate() can enforce the limit
                if tool_name == "delegate":
                    args["_depth"] = _depth + 1

                try:
                    result = TOOLS[tool_name]["func"](**args)
                    mem.add("tool", f"Tool {tool_name} returned: {result}")
                    results.append(str(result))
                except Exception as e:
                    logger.error(f"Tool execution error ({tool_name}): {e}")
                    results.append(f"Tool error: {e}")

            tool_result_msg = "\n".join(results)
            followup = messages + [{"role": "tool", "content": tool_result_msg}]
            try:
                final_response, _ = self.provider.chat(followup, self.model)
                mem.add("assistant", final_response)
                return final_response
            except Exception as e:
                logger.error(f"LLM second call error: {e}")
                return f"Tool executed but error getting response: {e}"

        mem.add("assistant", response)
        return response