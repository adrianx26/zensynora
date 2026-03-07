from .memory import Memory
from .provider import LLMProvider
from .tools import TOOLS
from rich.console import Console
import logging

console = Console()
logger = logging.getLogger(__name__)

class Agent:
    def __init__(self, config):
        self.memory = Memory()
        self.provider = LLMProvider(config)
        self.model = config.get("agents", {}).get("defaults", {}).get("model", "llama3.2")

    def think(self, user_message: str) -> str:
        self.memory.add("user", user_message)
        history = self.memory.get_history()

        # Simple ReAct-style prompt
        prompt = f"""You are MyClaw, a personal AI agent.
You can use tools: shell, read_file, write_file.

History:
{json.dumps(history, ensure_ascii=False, indent=2)}

New message: {user_message}

Reply directly or call tools in JSON format: {{"tool": "shell", "args": {{"cmd": "ls"}}}}"""

        try:
            response = self.provider.chat([{"role": "user", "content": prompt}], self.model)
        except Exception as e:
            logger.error(f"LLM provider error: {e}")
            self.memory.add("system", f"Error communicating with AI: {e}")
            return f"Sorry, I encountered an error: {e}"

        # Simplu parsing tool (poți extinde cu tool calling real)
        if '{"tool"' in response:
            try:
                tool_call = json.loads(response[response.find('{'):response.rfind('}')+1])
                tool_name = tool_call["tool"]
                args = tool_call.get("args", {})
                
                # Validate tool exists
                if tool_name not in TOOLS:
                    return f"Unknown tool: {tool_name}"
                
                result = TOOLS[tool_name]["func"](**args)
                self.memory.add("tool", f"Tool {tool_name} returned: {result}")
                # al doilea call LLM cu rezultat
                try:
                    return self.provider.chat([{"role": "user", "content": f"Tool result: {result}\nFinal answer:"}])
                except Exception as e:
                    logger.error(f"LLM second call error: {e}")
                    return f"Tool executed but error getting response: {e}"
            except json.JSONDecodeError as e:
                logger.error(f"JSON parse error: {e}")
                return f"Could not parse tool call: {e}"
            except Exception as e:
                logger.error(f"Tool execution error: {e}")
                return f"Tool execution failed: {e}"

        self.memory.add("assistant", response)
        return response