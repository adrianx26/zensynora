"""
LLM provider layer for MyClaw.

Supported providers
───────────────────
Local
  ollama      – Ollama  (http://localhost:11434)
  lmstudio    – LM Studio  (OpenAI-compat, http://localhost:1234/v1)
  llamacpp    – llama-server  (OpenAI-compat, http://localhost:8080/v1)

Online
  openai      – OpenAI  (gpt-4o, gpt-4-turbo, …)
  anthropic   – Anthropic Claude  (claude-3-5-sonnet, …)
  gemini      – Google Gemini  (gemini-1.5-pro, …)
  groq        – Groq  (llama3-70b-8192, mixtral-8x7b-32768, …)
  openrouter  – OpenRouter  (any model via openrouter.ai)

Select with  agents.defaults.provider  (or per named-agent  provider  field)
in ~/.myclaw/config.json.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60  # seconds

# ── Tool Schemas (used by providers that support OpenAI-style function calling) ──

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
    # ── Sub-Agent Delegation ──────────────────────────────────────────────────
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
    # ── Dynamic Tool Building ─────────────────────────────────────────────────
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
    # ── Scheduling ────────────────────────────────────────────────────────────
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


# ── Helpers ────────────────────────────────────────────────────────────────────

def _openai_tool_calls_to_dict(tool_calls) -> Optional[List[Dict]]:
    """Convert openai SDK ToolCall objects to the dict format agent.py expects."""
    if not tool_calls:
        return None
    result = []
    for tc in tool_calls:
        import json as _json
        args = tc.function.arguments
        if isinstance(args, str):
            try:
                args = _json.loads(args)
            except Exception:
                args = {}
        result.append({
            "function": {
                "name": tc.function.name,
                "arguments": args,
            }
        })
    return result or None


# ── Abstract Base ──────────────────────────────────────────────────────────────

class BaseLLMProvider(ABC):
    """All providers implement this interface."""

    @abstractmethod
    def chat(
        self,
        messages: List[Dict],
        model: str,
    ) -> Tuple[str, Optional[List[Dict]]]:
        """Send messages to the LLM.

        Returns:
            (response_text, tool_calls)
            tool_calls is None when no tools were invoked.
        """


# ── Local Providers ────────────────────────────────────────────────────────────

class OllamaProvider(BaseLLMProvider):
    """Ollama native API (http://localhost:11434)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            self.base_url = config.providers.ollama.base_url
        except Exception:
            self.base_url = "http://localhost:11434"
        self.timeout = timeout

    def chat(self, messages, model="llama3.2"):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "tools": TOOL_SCHEMAS,
        }
        try:
            r = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
            msg = r.json()["message"]
            tool_calls = msg.get("tool_calls") or None
            return msg.get("content", ""), tool_calls
        except requests.Timeout:
            raise TimeoutError(f"Ollama request timed out after {self.timeout}s")
        except requests.ConnectionError as e:
            raise ConnectionError(f"Could not connect to Ollama at {self.base_url}") from e
        except requests.HTTPError as e:
            raise RuntimeError(f"Ollama HTTP error: {e}") from e


class OpenAICompatProvider(BaseLLMProvider):
    """
    Generic OpenAI-compatible provider.

    Works for: LM Studio, llama.cpp server, Groq, OpenRouter — all expose
    the same /chat/completions endpoint with OpenAI request/response schema.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: int = DEFAULT_TIMEOUT,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "The 'openai' package is required for this provider.\n"
                "Install it with:  pip install openai"
            )
        self.client = OpenAI(
            api_key=api_key or "no-key",
            base_url=base_url,
            timeout=timeout,
            default_headers=extra_headers or {},
        )

    def chat(self, messages, model="gpt-4o-mini"):
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )
        msg = response.choices[0].message
        tool_calls = _openai_tool_calls_to_dict(msg.tool_calls)
        return msg.content or "", tool_calls


class LMStudioProvider(OpenAICompatProvider):
    """LM Studio local server (OpenAI-compat, default http://localhost:1234/v1)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            cfg = config.providers.lmstudio
            base_url = cfg.base_url
            api_key  = cfg.api_key.get_secret_value()
        except Exception:
            base_url = "http://localhost:1234/v1"
            api_key  = "lm-studio"
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout)


class LlamaCppProvider(OpenAICompatProvider):
    """llama-server (llama.cpp) — OpenAI-compat, default http://localhost:8080/v1."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            cfg = config.providers.llamacpp
            base_url = cfg.base_url
            api_key  = cfg.api_key.get_secret_value()
        except Exception:
            base_url = "http://localhost:8080/v1"
            api_key  = "no-key"
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout)


# ── Online Providers ───────────────────────────────────────────────────────────

class OpenAIProvider(OpenAICompatProvider):
    """OpenAI cloud (gpt-4o, gpt-4-turbo, gpt-3.5-turbo, …)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            cfg = config.providers.openai
            api_key  = cfg.api_key.get_secret_value()
            base_url = cfg.base_url
        except Exception:
            api_key  = ""
            base_url = "https://api.openai.com/v1"
        if not api_key:
            raise ValueError("openai.api_key is not set in config.")
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout)


class GroqProvider(OpenAICompatProvider):
    """Groq cloud (llama3-70b-8192, mixtral-8x7b-32768, gemma-7b-it, …)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            cfg = config.providers.groq
            api_key  = cfg.api_key.get_secret_value()
            base_url = cfg.base_url
        except Exception:
            api_key  = ""
            base_url = "https://api.groq.com/openai/v1"
        if not api_key:
            raise ValueError("groq.api_key is not set in config.")
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout)


class OpenRouterProvider(OpenAICompatProvider):
    """OpenRouter cloud — routes to 100+ models via a single API."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            cfg = config.providers.openrouter
            api_key   = cfg.api_key.get_secret_value()
            base_url  = cfg.base_url
            site_url  = cfg.site_url
            site_name = cfg.site_name
        except Exception:
            api_key   = ""
            base_url  = "https://openrouter.ai/api/v1"
            site_url  = ""
            site_name = ""
        if not api_key:
            raise ValueError("openrouter.api_key is not set in config.")
        headers = {}
        if site_url:
            headers["X-OpenRouter-Site-URL"] = site_url
        if site_name:
            headers["X-OpenRouter-Title"] = site_name
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout, extra_headers=headers)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude (claude-3-5-sonnet-20241022, claude-3-haiku-20240307, …)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError(
                "The 'anthropic' package is required.\n"
                "Install it with:  pip install anthropic"
            )
        try:
            api_key = config.providers.anthropic.api_key.get_secret_value()
        except Exception:
            api_key = ""
        if not api_key:
            raise ValueError("anthropic.api_key is not set in config.")
        self.client  = Anthropic(api_key=api_key)
        self.timeout = timeout

    def chat(self, messages, model="claude-3-5-sonnet-20241022"):
        import json as _json

        # Anthropic separates the system prompt from the conversation
        system_content = ""
        conv_messages  = []
        for m in messages:
            role = m["role"]
            content = m.get("content", "")
            if role == "system":
                system_content += content + "\n"
            elif role in ("user", "assistant"):
                conv_messages.append({"role": role, "content": content})
            elif role == "tool":
                # Append tool result as a user turn
                conv_messages.append({"role": "user", "content": f"[tool result] {content}"})

        # Build Anthropic tool definitions
        ant_tools = []
        for ts in TOOL_SCHEMAS:
            f = ts["function"]
            ant_tools.append({
                "name":        f["name"],
                "description": f.get("description", ""),
                "input_schema": f.get("parameters", {"type": "object", "properties": {}}),
            })

        kwargs = dict(
            model=model,
            max_tokens=4096,
            messages=conv_messages,
            tools=ant_tools,
        )
        if system_content.strip():
            kwargs["system"] = system_content.strip()

        response = self.client.messages.create(**kwargs)

        text_parts  = []
        tool_calls  = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                tool_calls.append({
                    "function": {
                        "name":      block.name,
                        "arguments": args,
                    }
                })

        return "\n".join(text_parts), (tool_calls or None)


class GeminiProvider(BaseLLMProvider):
    """Google Gemini (gemini-1.5-pro, gemini-1.5-flash, gemini-2.0-flash, …)."""

    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "The 'google-generativeai' package is required.\n"
                "Install it with:  pip install google-generativeai"
            )
        try:
            api_key = config.providers.gemini.api_key.get_secret_value()
        except Exception:
            api_key = ""
        if not api_key:
            raise ValueError("gemini.api_key is not set in config.")
        genai.configure(api_key=api_key)
        self._genai    = genai
        self.timeout   = timeout

    def _build_tools(self):
        """Convert TOOL_SCHEMAS to Gemini FunctionDeclaration list."""
        from google.generativeai.types import content_types
        declarations = []
        for ts in TOOL_SCHEMAS:
            f = ts["function"]
            declarations.append(
                self._genai.protos.FunctionDeclaration(
                    name=f["name"],
                    description=f.get("description", ""),
                    parameters=self._genai.protos.Schema(
                        type=self._genai.protos.Type.OBJECT,
                        properties={
                            k: self._genai.protos.Schema(
                                type=self._genai.protos.Type.STRING,
                                description=v.get("description", ""),
                            )
                            for k, v in f.get("parameters", {}).get("properties", {}).items()
                        },
                        required=f.get("parameters", {}).get("required", []),
                    ),
                )
            )
        return [self._genai.protos.Tool(function_declarations=declarations)]

    def chat(self, messages, model="gemini-1.5-flash"):
        system_parts = []
        history      = []
        last_user    = None

        for m in messages:
            role    = m["role"]
            content = m.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                last_user = content
                history.append({"role": "user", "parts": [content]})
            elif role == "assistant":
                history.append({"role": "model", "parts": [content]})
            elif role == "tool":
                history.append({"role": "user", "parts": [f"[tool result] {content}"]})

        gen_model = self._genai.GenerativeModel(
            model_name=model,
            system_instruction="\n".join(system_parts) if system_parts else None,
            tools=self._build_tools(),
        )

        chat_session = gen_model.start_chat(history=history[:-1] if history else [])
        response     = chat_session.send_message(last_user or "")

        text_parts = []
        tool_calls = []

        for part in response.parts:
            if hasattr(part, "text") and part.text:
                text_parts.append(part.text)
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                tool_calls.append({
                    "function": {
                        "name":      fc.name,
                        "arguments": dict(fc.args),
                    }
                })

        return "\n".join(text_parts), (tool_calls or None)


# ── Provider Factory ──────────────────────────────────────────────────────────

_PROVIDER_MAP = {
    "ollama":     OllamaProvider,
    "lmstudio":   LMStudioProvider,
    "llamacpp":   LlamaCppProvider,
    "openai":     OpenAIProvider,
    "anthropic":  AnthropicProvider,
    "gemini":     GeminiProvider,
    "groq":       GroqProvider,
    "openrouter": OpenRouterProvider,
}

SUPPORTED_PROVIDERS = list(_PROVIDER_MAP.keys())


def get_provider(config, provider_name: str = "ollama") -> BaseLLMProvider:
    """Return an initialised provider instance for *provider_name*.

    Raises:
        ValueError  – unknown provider name
        ImportError – required SDK not installed
        ValueError  – API key missing for cloud providers
    """
    name = (provider_name or "ollama").lower().strip()
    cls  = _PROVIDER_MAP.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown provider '{name}'. "
            f"Supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    logger.debug(f"Initialising provider: {name}")
    return cls(config)


# ── Legacy alias (keeps old import `from .provider import LLMProvider` working) ─

LLMProvider = OllamaProvider