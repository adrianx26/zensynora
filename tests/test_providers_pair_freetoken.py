"""2026-11-08: NVIDIA PAIR (Personal AI Router) and FreeToken (FlashML) as built-in providers.

PAIR: one local, OpenAI-compatible endpoint (http://127.0.0.1:11434/v1 by default — it also
proxies LM Studio :1234 and llama.cpp :8080) that routes requests to the free GPU at home; loopback
only, no key. FreeToken: `ft serve` / FreeToken Desktop, OpenAI-compatible on
http://127.0.0.1:1919/v1, no key."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("name,url", [("nvpair", "http://127.0.0.1:11434/v1"),
                                      ("freetoken", "http://127.0.0.1:1919/v1")])
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


def test_listed_as_local_everywhere():
    from myclaw.doctor.checks import LOCAL_PROVIDERS
    from myclaw.offline import _LOCAL_PROVIDERS
    from myclaw.onboard import _PROVIDER_HINTS as PROVIDERS

    for name in ("nvpair", "freetoken"):
        assert name in LOCAL_PROVIDERS and name in _LOCAL_PROVIDERS and name in PROVIDERS
