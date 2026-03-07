import requests
import logging
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30  # seconds

# ── Tool Schemas (native Ollama function calling) ──────────────────────────────
# Passed on every /api/chat call so the model knows what tools exist.
# _depth is intentionally omitted — it is injected by agent.py, not the LLM.

TOOL_SCHEMAS = [
    # ── Core ─────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Execute an allowed shell command in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command to run"}
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
                    "path":    {"type": "string", "description": "Relative path within workspace"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    # ── Feature 3: Sub-Agent Delegation ──────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": "Delegate a task to another named agent and return its response.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string", "description": "Name of the target agent"},
                    "task":       {"type": "string", "description": "Instruction for the target agent"}
                },
                "required": ["agent_name", "task"]
            }
        }
    },
    # ── Feature 4: Dynamic Tool Building ─────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_tools",
            "description": "Return a list of all currently available tool names.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "register_tool",
            "description": (
                "Write a Python function as source code and register it as a new tool. "
                "The function name must match the 'name' argument. "
                "Use \\n for newlines in the code string."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Tool name (valid Python identifier)"},
                    "code": {"type": "string", "description": "Full Python source of the function"}
                },
                "required": ["name", "code"]
            }
        }
    },
    # ── Feature 5: Agent-Initiated Scheduling ────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "schedule",
            "description": (
                "Schedule a task to run in the future. "
                "Use 'delay' for a one-shot job (fires once after N seconds). "
                "Use 'every' for a recurring job (fires every N seconds). "
                "The task will be executed by the agent and the result sent back via Telegram."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task":    {"type": "string",  "description": "Instruction to execute at trigger time"},
                    "delay":   {"type": "integer", "description": "Seconds until one-shot execution"},
                    "every":   {"type": "integer", "description": "Recurring interval in seconds"},
                    "user_id": {"type": "string",  "description": "User ID for memory context"}
                },
                "required": ["task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_schedule",
            "description": "Cancel an active scheduled job by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID returned by schedule()"}
                },
                "required": ["job_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_schedules",
            "description": "List all currently active scheduled jobs.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


class LLMProvider:
    """Ollama LLM provider with native tool calling, timeout, and error handling."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            self.base_url = config.providers.ollama.base_url
        except Exception:
            self.base_url = "http://localhost:11434"
        self.timeout = timeout

    def chat(
        self,
        messages: List[Dict],
        model: str = "llama3.2"
    ) -> Tuple[str, Optional[List[Dict]]]:
        """Send chat request to Ollama with native tool calling.

        Returns:
            (response_text, tool_calls) — tool_calls is None when no tools invoked.
        """
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "tools": TOOL_SCHEMAS
        }
        try:
            r = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout
            )
            r.raise_for_status()
            msg = r.json()["message"]
            tool_calls  = msg.get("tool_calls") or None
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