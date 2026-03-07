from .memory import Memory
from .provider import LLMProvider
from .tools import TOOLS
from rich.console import Console

console = Console()

class Agent:
    def __init__(self, config):
        self.memory = Memory()
        self.provider = LLMProvider(config)
        self.model = config.get("agents", {}).get("defaults", {}).get("model", "llama3.2")

    def think(self, user_message: str) -> str:
        self.memory.add("user", user_message)
        history = self.memory.get_history()

        # Prompt simplu ReAct-style
        prompt = f"""Tu ești MyClaw, un agent AI personal.
Poți folosi tool-uri: shell, read_file, write_file.

Istoric:
{json.dumps(history, ensure_ascii=False, indent=2)}

Mesaj nou: {user_message}

Răspunde direct sau cheamă tool-uri în format JSON: {{"tool": "shell", "args": {{"cmd": "ls"}}}}"""

        response = self.provider.chat([{"role": "user", "content": prompt}], self.model)

        # Simplu parsing tool (poți extinde cu tool calling real)
        if '{"tool"' in response:
            try:
                tool_call = json.loads(response[response.find('{'):response.rfind('}')+1])
                tool_name = tool_call["tool"]
                args = tool_call.get("args", {})
                result = TOOLS[tool_name]["func"](**args)
                self.memory.add("tool", f"Tool {tool_name} returned: {result}")
                # al doilea call LLM cu rezultat
                return self.provider.chat([{"role": "user", "content": f"Tool result: {result}\nFinal answer:"}])
            except:
                pass

        self.memory.add("assistant", response)
        return response