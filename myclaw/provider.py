import requests
import json
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

# Default timeout for LLM requests
DEFAULT_TIMEOUT = 30  # seconds

class LLMProvider:
    """Ollama LLM provider with timeout and error handling."""
    
    def __init__(self, config, timeout: int = DEFAULT_TIMEOUT):
        self.config = config.get("providers", {}).get("ollama", {})
        self.base_url = self.config.get("base_url", "http://localhost:11434")
        self.timeout = timeout

    def chat(self, messages: List[Dict], model: str = "llama3.2") -> str:
        """Send chat request to Ollama with timeout."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "tools": []  # vom adăuga mai târziu tool calling real
        }
        
        try:
            r = requests.post(
                f"{self.base_url}/api/chat", 
                json=payload,
                timeout=self.timeout
            )
            r.raise_for_status()
            return r.json()["message"]["content"]
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