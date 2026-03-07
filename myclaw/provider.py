import requests
import json
import logging
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

# Default timeout for LLM requests
DEFAULT_TIMEOUT = 30  # seconds

# JSON Schema definitions for all tools — passed to Ollama for native function calling
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Execute an allowed shell command in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "The shell command to run"}
                },
                "required": ["cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path within workspace"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file in the workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path within workspace"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        }
    }
]


class LLMProvider:
    """Ollama LLM provider with native tool calling, timeout, and error handling."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        self.config = config.get("providers", {}).get("ollama", {})
        self.base_url = self.config.get("base_url", "http://localhost:11434")
        self.timeout = timeout

    def chat(
        self,
        messages: List[Dict],
        model: str = "llama3.2"
    ) -> Tuple[str, Optional[List[Dict]]]:
        """Send chat request to Ollama with native tool calling.

        Returns:
            (response_text, tool_calls) — tool_calls is None if no tools were called,
            otherwise a list of Ollama tool_call objects.
        """
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "tools": TOOL_SCHEMAS  # native function calling schema
        }

        try:
            r = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout
            )
            r.raise_for_status()
            msg = r.json()["message"]

            # Native tool calls from Ollama (model was fine-tuned to emit these)
            tool_calls = msg.get("tool_calls") or None
            response_text = msg.get("content", "")

            return response_text, tool_calls

        except requests.Timeout:
            logger.error(f"LLM request timed out after {self.timeout}s")
            raise TimeoutError(f"LLM request timed out after {self.timeout} seconds")
        except requests.ConnectionError as e:
            logger.error(f"Connection error to Ollama: {e}")
            raise ConnectionError(f"Could not connect to Ollama at {self.base_url}") from e
        except requests.HTTPError as e:
            logger.error(f"HTTP error from Ollama: {e}")
            raise RuntimeError(f"Ollama error: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error in LLM call: {e}")
            raise