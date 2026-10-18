"""Per-agent context length: estimate, model maximum, trimming, Ollama num_ctx, API."""
import asyncio

import pytest


def test_estimate_grows_with_the_prompt_and_min_below_recommended():
    from myclaw.config import AppConfig
    from myclaw.context_budget import estimate_context

    cfg = AppConfig()
    short = estimate_context(cfg, "default", "You are terse.")
    long = estimate_context(cfg, "default", "You are thorough. " * 2000)
    assert short["min"] < short["recommended"]
    assert long["parts"]["prompt"] > short["parts"]["prompt"] + 8000
    assert long["min"] > short["min"] and long["recommended"] > short["recommended"]
    assert short["min"] % 512 == 0 and short["recommended"] % 1024 == 0
    # the tool definitions are part of every request
    assert short["parts"]["tools"] > 0 and short["tool_count"] > 10
    # a longer conversation before summarising needs more room
    cfg.agents.summarization_threshold = 40
    assert estimate_context(cfg, "default", "You are terse.")["recommended"] > short["recommended"]


def test_fit_messages_drops_oldest_and_orphan_tool_results(monkeypatch):
    import myclaw.context_budget as cb

    monkeypatch.setattr(cb, "_tool_schema_tokens", lambda: (0, 0))
    big = "x" * 4000  # ≈ 1053 tokens
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": big},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": big},
            {"role": "user", "content": big},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "latest"}]
    out, dropped = cb.fit_messages(msgs, 1800, reserve=200)
    assert out[0]["role"] == "system" and out[-1]["content"] == "latest"
    assert out[1]["role"] != "tool"  # no tool result without its call
    assert dropped >= 3
    assert cb.fit_messages(msgs, 0) == (msgs, 0)
    # never drops the newest message, even if it alone is too big
    out, _ = cb.fit_messages([{"role": "system", "content": "s"}, {"role": "user", "content": big * 5}], 1000)
    assert len(out) == 2


def test_model_max_from_openai_compatible_list(monkeypatch):
    import myclaw.context_budget as cb
    from myclaw.config import AppConfig

    async def fake_ask(ep, model):
        assert ep["kind"] == "openai"
        return cb._find_ctx({"id": model, "top_provider": {"context_length": 262144}})

    monkeypatch.setattr(cb, "_ask_provider", fake_ask)
    cb._MAX_CACHE.clear()
    out = asyncio.run(cb.model_context_max(AppConfig(), "openrouter", "google/gemma-4:free"))
    assert out == {"max": 262144, "source": "provider"}


def test_model_max_falls_back_to_library_then_unknown(monkeypatch):
    import myclaw.context_budget as cb
    from myclaw.config import AppConfig

    async def down(ep, model):
        raise ConnectionError("server down")

    monkeypatch.setattr(cb, "_ask_provider", down)
    cb._MAX_CACHE.clear()
    assert asyncio.run(cb.model_context_max(AppConfig(), "openai", "gpt-4o"))["source"] == "library"
    out = asyncio.run(cb.model_context_max(AppConfig(), "ollama", "some-unknown-model"))
    assert out["max"] is None and out["source"] == "unknown" and "server down" in out["error"]


def test_find_ctx_reads_known_fields():
    from myclaw.context_budget import _find_ctx

    assert _find_ctx({"max_context_length": 32768}) == 32768          # LM Studio
    assert _find_ctx({"default_generation_settings": {"n_ctx": 8192}}) == 8192  # llama.cpp /props
    assert _find_ctx({"context_window": 131072}) == 131072             # Groq
    assert _find_ctx({"inputTokenLimit": 1048576}) == 1048576          # Gemini
    assert _find_ctx({"id": "x"}) is None


def test_config_field_and_agent_lookup():
    from myclaw.config import AppConfig
    from myclaw.context_budget import agent_context_length

    cfg = AppConfig(**{"agents": {"defaults": {"context_length": 16384},
                                  "named": [{"name": "Coder", "context_length": 32768}, {"name": "Plain"}]}})
    assert agent_context_length(cfg, "default") == 16384
    assert agent_context_length(cfg, "Coder") == 32768
    assert agent_context_length(cfg, "Plain") == 0
    with pytest.raises(Exception):
        AppConfig(**{"agents": {"defaults": {"context_length": -1}}})


def test_ollama_gets_num_ctx(monkeypatch):
    import myclaw.provider as prov_mod
    from myclaw.context_budget import CONTEXT_LENGTH

    sent = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "hi"}}

    class C:
        async def post(self, url, json):
            sent.update(json)
            return R()

    monkeypatch.setattr(prov_mod.HTTPClientPool, "get_client", staticmethod(lambda *a, **k: C()))
    monkeypatch.setattr(prov_mod, "_get_configured_semantic_cache", lambda: None)
    p = prov_mod.OllamaProvider.__new__(prov_mod.OllamaProvider)
    p.base_url, p.timeout = "http://x", 5

    async def run(n):
        token = CONTEXT_LENGTH.set(n)
        try:
            return await p.chat([{"role": "user", "content": "hi"}], "llama3.2")
        finally:
            CONTEXT_LENGTH.reset(token)

    asyncio.run(run(8192))
    assert sent["options"] == {"num_ctx": 8192}
    sent.clear()
    asyncio.run(run(0))
    assert "options" not in sent


def test_estimate_api(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import myclaw.config as cfg_mod
    import myclaw.context_budget as cb
    from myclaw.web import models_api

    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", tmp_path / "config.json")
    cfg_mod._cached_config = None

    async def fake_max(config, provider, model, key=""):
        return {"max": 40960, "source": "provider"}

    monkeypatch.setattr(cb, "model_context_max", fake_max)
    app = FastAPI()
    app.include_router(models_api.router)
    r = TestClient(app).post("/api/agents/context-estimate",
                             json={"name": "Coder", "system_prompt": "You review code.", "provider": "ollama", "model": "qwen3"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_max"] == 40960 and body["slider_max"] == 40960
    assert body["min"] < body["recommended"] <= 40960 * 2
    assert set(body["parts"]) == {"prompt", "skills", "team", "tools"}
    cfg_mod._cached_config = None
