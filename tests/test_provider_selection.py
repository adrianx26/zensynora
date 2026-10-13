"""Regression tests for the "chat goes to Ollama / 404 /api/chat" bug.

The user selected llama.cpp in Settings; the `openai` SDK was missing, so the
agent silently switched to Ollama on localhost:11434 and chat failed with a
404 that had nothing to do with the chosen provider.
"""
import types

import pytest

from myclaw.exceptions import ProviderUnavailableError


def _cfg(provider="llamacpp", model="m"):
    from myclaw.config import AppConfig

    cfg = AppConfig()
    cfg.agents.defaults.provider = provider
    cfg.agents.defaults.model = model
    return cfg


def test_agent_does_not_silently_switch_to_ollama(monkeypatch):
    import myclaw.agent as agent_mod

    def boom(config, name):
        raise ImportError("The 'openai' package is required for this provider.")

    monkeypatch.setattr(agent_mod, "get_provider", boom)
    a = agent_mod.Agent(_cfg(), name="default")
    with pytest.raises(ProviderUnavailableError) as exc:
        _ = a.provider
    assert "llamacpp" in str(exc.value)
    assert a._provider_name == "llamacpp"  # not rewritten to "ollama"


def test_fallback_never_reuses_failed_primary(monkeypatch):
    import myclaw.offline as offline

    picked = []

    def fake_get_provider(config, name):
        picked.append(name)
        return types.SimpleNamespace(name=name)

    monkeypatch.setattr(offline, "get_provider", fake_get_provider)
    p = offline.get_fallback_provider(_cfg(), prefer_cloud=False, exclude="ollama")
    assert p.name == "lmstudio"
    assert "ollama" not in picked


class _RejectingCompletions:
    """Mimics llama-server without --jinja: 500 whenever `tools` is sent."""

    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if "tools" in kwargs:
            err = Exception("Error code: 500 - tools param requires --jinja flag")
            err.status_code = 500
            raise err
        msg = types.SimpleNamespace(content="hello", tool_calls=None)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=None)


@pytest.mark.asyncio
async def test_openai_compat_retries_without_tools(monkeypatch):
    pytest.importorskip("openai")
    import myclaw.provider as prov

    monkeypatch.setattr(prov, "_get_configured_semantic_cache", lambda: None)
    p = prov.LlamaCppProvider(_cfg())
    completions = _RejectingCompletions()
    p.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))

    text, tools = await p.chat([{"role": "user", "content": "hi"}], "m")
    assert text == "hello"
    assert "tools" in completions.calls[0] and "tools" not in completions.calls[1]

    # Remembered: the next request goes straight out without tools.
    await p.chat([{"role": "user", "content": "again"}], "m")
    assert "tools" not in completions.calls[2]


@pytest.mark.asyncio
async def test_probe_reports_missing_sdk(monkeypatch):
    from myclaw.web import settings_api

    monkeypatch.setattr(settings_api, "_missing_sdk", lambda provider: "openai")
    res = await settings_api._probe("llamacpp", "http://127.0.0.1:9/v1", None)
    assert res["ok"] is False
    assert "pip install openai" in res["error"]
