"""2026-11-08: NVIDIA PAIR (Personal AI Router) and FreeToken (FlashML) as built-in providers.

PAIR: one local, OpenAI-compatible endpoint (http://127.0.0.1:11434/v1 by default — it also
proxies LM Studio :1234 and llama.cpp :8080) that routes requests to the free GPU at home; loopback
only, no key. FreeToken: `ft serve` / FreeToken Desktop, OpenAI-compatible on
http://127.0.0.1:1919/v1, no key."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("name,url", [("nvpair", "http://127.0.0.1:11434/v1"),
                                      ("freetoken", "http://127.0.0.1:1919/v1"),
                                      ("bionic", "http://127.0.0.1:8080/v1")])
def test_builtin_local_openai_compatible(name, url):
    from myclaw.config import AppConfig
    from myclaw.provider import (LOCAL_BUILTIN_PROVIDERS, SUPPORTED_PROVIDERS, OpenAICompatProvider,
                                 get_provider, provider_endpoint, provider_names)

    cfg = AppConfig()
    assert name in SUPPORTED_PROVIDERS and name in LOCAL_BUILTIN_PROVIDERS and name in provider_names(cfg)
    ep = provider_endpoint(cfg, name)
    assert ep["kind"] == "openai" and ep["base_url"] == url and ep["local"]
    prov = get_provider(cfg, name)
    assert isinstance(prov, OpenAICompatProvider) and str(prov.client.base_url).rstrip("/") == url
    getattr(cfg.providers, name).base_url = "http://127.0.0.1:1235"      # a moved port, without /v1
    assert provider_endpoint(cfg, name)["base_url"] == "http://127.0.0.1:1235/v1"


@pytest.mark.parametrize("name,url", [
    ("ollama_cloud", "https://ollama.com"),
    ("copilot", "https://api.githubcopilot.com"),
    ("github", "https://models.github.ai/inference"),
    ("kilo", "https://api.kilo.ai/api/gateway"),
    ("opencode", "https://opencode.ai/zen/v1"),
])
def test_builtin_cloud_openai_compatible(name, url):
    from pydantic import SecretStr
    from myclaw.config import AppConfig
    from myclaw.provider import (LOCAL_BUILTIN_PROVIDERS, SUPPORTED_PROVIDERS, OpenAICompatProvider,
                                 OllamaCloudProvider, get_provider, provider_endpoint, provider_names)

    cfg = AppConfig()
    assert name in SUPPORTED_PROVIDERS and name not in LOCAL_BUILTIN_PROVIDERS and name in provider_names(cfg)
    ep = provider_endpoint(cfg, name)
    expected_kind = "ollama" if name == "ollama_cloud" else "openai"
    assert ep["kind"] == expected_kind and ep["base_url"] == url and not ep["local"]
    with pytest.raises(ValueError, match="api_key is not set"):
        get_provider(cfg, name)
    getattr(cfg.providers, name).api_key = SecretStr("test-key")
    prov = get_provider(cfg, name)
    expected_cls = OllamaCloudProvider if name == "ollama_cloud" else OpenAICompatProvider
    assert isinstance(prov, expected_cls)


def test_lmstudio_models_and_compatible_llm_settings():
    from myclaw.config import AppConfig
    from myclaw.provider import CompatibleProvider, get_provider, provider_endpoint

    cfg = AppConfig()
    cfg.providers.lmstudio.models = ["qwen2.5-coder-7b", "llama-3.2-3b"]
    ep_lm = provider_endpoint(cfg, "lmstudio")
    assert ep_lm["models"] == ["qwen2.5-coder-7b", "llama-3.2-3b"]

    cfg.providers.compatible.base_url = "http://127.0.0.1:8000"
    cfg.providers.compatible.models = ["custom-model"]
    cfg.providers.compatible.temperature = 0.2
    cfg.providers.compatible.max_tokens = 2048
    cfg.providers.compatible.timeout_seconds = 45
    cfg.providers.compatible.local = True
    ep_c = provider_endpoint(cfg, "compatible")
    assert ep_c["base_url"] == "http://127.0.0.1:8000/v1"
    assert ep_c["local"] is True
    assert ep_c["models"] == ["custom-model"]
    prov = get_provider(cfg, "compatible")
    assert isinstance(prov, CompatibleProvider)
    assert prov.temperature == 0.2
    assert prov.max_tokens == 2048


def test_listed_as_local_everywhere():
    from myclaw.doctor.checks import LOCAL_PROVIDERS
    from myclaw.offline import _LOCAL_PROVIDERS
    from myclaw.onboard import _PROVIDER_HINTS as PROVIDERS

    for name in ("nvpair", "freetoken", "bionic"):
        assert name in LOCAL_PROVIDERS and name in _LOCAL_PROVIDERS and name in PROVIDERS
    for name in ("copilot", "github", "kilo", "opencode"):
        assert name not in LOCAL_PROVIDERS and name in PROVIDERS


# ── 2026-11-18: the connection test of a FreeToken server on another computer ──
def _fake_get(monkeypatch, outcomes):
    import httpx

    from myclaw.web import settings_api

    calls = []

    async def get(self, url, headers=None, params=None):
        calls.append(url)
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return httpx.Response(200, json={"data": [{"id": "glm"}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(settings_api, "_missing_sdk", lambda provider: "")
    return calls


@pytest.mark.asyncio
async def test_probe_timeout_on_lan_server_names_the_fix(monkeypatch):
    import httpx

    from myclaw.web import settings_api

    calls = _fake_get(monkeypatch, [httpx.ConnectTimeout("t"), httpx.ConnectTimeout("t")])
    res = await settings_api._probe("freetoken", "http://192.168.8.102:1919/v1", None)
    assert res["ok"] is False and len(calls) == settings_api.PROBE_ATTEMPTS      # one retry
    assert "192.168.8.102:1919" in res["error"] and "--host 0.0.0.0" in res["error"]
    assert "firewall" in res["error"]


@pytest.mark.asyncio
async def test_probe_retry_succeeds_when_the_server_was_slow(monkeypatch):
    import httpx

    from myclaw.web import settings_api

    calls = _fake_get(monkeypatch, [httpx.ConnectTimeout("t"), None])
    res = await settings_api._probe("freetoken", "http://192.168.8.102:1919/v1", None)
    assert res["ok"] is True and res["models"] == ["glm"] and len(calls) == 2


@pytest.mark.asyncio
async def test_probe_refused_port_is_not_retried_and_says_so(monkeypatch):
    import httpx

    from myclaw.web import settings_api

    calls = _fake_get(monkeypatch, [httpx.ConnectError("[Errno 111] Connection refused")])
    res = await settings_api._probe("freetoken", "http://192.168.8.102:1919/v1", None)
    assert res["ok"] is False and len(calls) == 1
    assert "nothing accepts connections" in res["error"] and "--host 0.0.0.0" in res["error"]


@pytest.mark.asyncio
async def test_probe_loopback_freetoken_suggests_starting_it(monkeypatch):
    import httpx

    from myclaw.web import settings_api

    _fake_get(monkeypatch, [httpx.ConnectError("[Errno 111] Connection refused")])
    res = await settings_api._probe("freetoken", "http://127.0.0.1:1919/v1", None)
    assert "Start FreeToken first" in res["error"] and "0.0.0.0" not in res["error"]
