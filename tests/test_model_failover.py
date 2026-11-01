"""2026-11-01: agents switch models on their own when a model or provider fails.

Live case: 9 agents sent ``cohere/north-mini-code:free`` to the Gemini provider
(404, model not found) and, having their own model, got no fallback at all.
Rules (asked of the owner): never block a workflow longer than 180 s, order
fallbacks automatically by measured health and latency, prefer a bigger
context on overflow, replace a model that is permanently unavailable in every
agent, and flag provider/model pairs that cannot work.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from tests.test_v071_features import home  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _fresh_health():
    from myclaw import model_health
    from myclaw.llm_errors import MODEL_COOLDOWN

    model_health.reset()
    MODEL_COOLDOWN.clear()
    yield
    model_health.reset()
    MODEL_COOLDOWN.clear()


def _choice(cfg, provider, model):
    return type(cfg.agents.rotation).model_fields["entries"].annotation.__args__[0](provider=provider, model=model)


class Prov:
    """Fake provider: per-model behaviour = text | Exception | ('sleep', seconds)."""

    def __init__(self, name, behaviour, calls):
        self.name, self.behaviour, self.calls = name, behaviour, calls

    async def chat(self, messages, model, stream=False):
        self.calls.append((self.name, model))
        b = self.behaviour.get((self.name, model), "ok")
        if isinstance(b, Exception):
            raise b
        if isinstance(b, tuple) and b[0] == "sleep":
            await asyncio.sleep(b[1])
        if stream:
            async def gen():
                yield f"answer from {self.name}/{model}"
            return gen(), []
        return f"answer from {self.name}/{model}", None


def _agent(home, monkeypatch, behaviour, name="Coder", own=("gemini", "cohere/north-mini-code:free"),  # noqa: F811
           rotation=(("openrouter", "nemotron:free"), ("lmstudio", "local"))):
    import myclaw.agent as agent_mod
    from myclaw.config import load_config

    cfg = load_config()
    cfg.agents.defaults.provider, cfg.agents.defaults.model = "openrouter", "default-model"
    cfg.agents.rotation.enabled = True
    cfg.agents.rotation.entries = [_choice(cfg, p, m) for p, m in rotation]
    named = type(cfg.agents).model_fields["named"].annotation.__args__[0]
    cfg.agents.named = [named(name=name, provider=own[0], model=own[1])]
    calls: list = []
    monkeypatch.setattr(agent_mod, "get_provider", lambda c, n, **k: Prov(n, behaviour, calls))
    return agent_mod.Agent(cfg, name=name, provider_name=own[0], model=own[1]), calls


MSG = [{"role": "user", "content": "hi"}]
NOT_FOUND = RuntimeError("Error code: 404 - models/cohere/north-mini-code:free is not found for API version v1main")


# ── fallback for every agent ─────────────────────────────────────────────────
def test_agent_with_its_own_model_falls_back(home, monkeypatch):  # noqa: F811
    a, calls = _agent(home, monkeypatch, {("gemini", "cohere/north-mini-code:free"): NOT_FOUND})
    cands = a._rotation_candidates()
    assert cands[0] == ("gemini", "cohere/north-mini-code:free")
    assert {("openrouter", "default-model"), ("openrouter", "nemotron:free"), ("lmstudio", "local")} <= set(cands)
    text, _ = asyncio.run(a._rotating_chat(cands, MSG, False))
    assert text.startswith("answer from openrouter/")
    assert a.last_model_used != ("gemini", "cohere/north-mini-code:free")


def test_streaming_falls_back_too(home, monkeypatch):  # noqa: F811
    a, calls = _agent(home, monkeypatch, {("gemini", "cohere/north-mini-code:free"): NOT_FOUND})

    async def go():
        gen, _ = await a._rotating_chat(a._rotation_candidates(), MSG, True)
        return "".join([c async for c in gen])

    assert asyncio.run(go()).startswith("answer from openrouter/")


# ── never block: per-attempt time limit and a total budget ───────────────────
def test_a_hanging_model_is_abandoned_in_time(home, monkeypatch):  # noqa: F811
    from myclaw import model_health

    monkeypatch.setattr(model_health, "ATTEMPT_SECONDS", 0.2)
    monkeypatch.setattr(model_health, "CALL_BUDGET_SECONDS", 1.0)
    a, calls = _agent(home, monkeypatch, {("gemini", "cohere/north-mini-code:free"): ("sleep", 30)})
    t0 = time.monotonic()
    text, _ = asyncio.run(a._rotating_chat(a._rotation_candidates(), MSG, False))
    assert time.monotonic() - t0 < 1.0 and not text.startswith("answer from gemini")
    hung = next(r for r in model_health.snapshot() if r["provider"] == "gemini")
    assert hung["failures"] == 1 and "timed out" in hung["last_error"]


def test_the_whole_call_stops_at_the_budget(home, monkeypatch):  # noqa: F811
    from myclaw import model_health
    from myclaw.agent import ProviderUnavailableError

    monkeypatch.setattr(model_health, "ATTEMPT_SECONDS", 0.3)
    monkeypatch.setattr(model_health, "CALL_BUDGET_SECONDS", 0.5)
    slow = ("sleep", 30)
    a, _ = _agent(home, monkeypatch, {("gemini", "cohere/north-mini-code:free"): slow,
                                      ("openrouter", "default-model"): slow, ("openrouter", "nemotron:free"): slow,
                                      ("lmstudio", "local"): slow})
    t0 = time.monotonic()
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(a._rotating_chat(a._rotation_candidates(), MSG, False))
    assert time.monotonic() - t0 < 1.0


# ── automatic ordering by health ─────────────────────────────────────────────
def test_order_prefers_healthy_and_fast_models():
    from myclaw import model_health as mh

    for _ in range(5):
        mh.record("p", "slow", True, 20.0)
        mh.record("p", "fast", True, 1.0)
        mh.record("p", "flaky", False, 1.0, "timed out")
    cands = [("p", "flaky"), ("p", "slow"), ("p", "fast"), ("p", "new")]
    order = mh.order(cands)
    assert order.index(("p", "fast")) < order.index(("p", "slow")) < order.index(("p", "flaky"))
    assert order.index(("p", "new")) < order.index(("p", "flaky"))       # unknown beats failing
    assert mh.order([("p", "a"), ("p", "b")]) == [("p", "a"), ("p", "b")]  # no data: your order


def test_own_model_stays_first_while_healthy(home, monkeypatch):  # noqa: F811
    from myclaw import model_health as mh

    a, _ = _agent(home, monkeypatch, {})
    for _ in range(3):
        mh.record("lmstudio", "local", True, 0.5)
    assert a._rotation_candidates()[0] == ("gemini", "cohere/north-mini-code:free")
    for _ in range(3):
        mh.record("gemini", "cohere/north-mini-code:free", False, 1.0, "timed out")
    assert a._rotation_candidates()[0] != ("gemini", "cohere/north-mini-code:free")


# ── context too long: prefer a bigger context ────────────────────────────────
def test_context_overflow_prefers_a_bigger_context(home, monkeypatch):  # noqa: F811
    from myclaw import model_health as mh

    sizes = {("openrouter", "default-model"): 8_000, ("openrouter", "nemotron:free"): 256_000,
             ("lmstudio", "local"): 4_000}
    monkeypatch.setattr(mh, "known_context", lambda p, m: sizes.get((p, m)))
    over = RuntimeError("This model's maximum context length is 8192 tokens (context_length_exceeded)")
    a, calls = _agent(home, monkeypatch, {("gemini", "cohere/north-mini-code:free"): over})
    text, _ = asyncio.run(a._rotating_chat(a._rotation_candidates(), MSG, False))
    assert text == "answer from openrouter/nemotron:free"


# ── permanent failures: replace the model in every agent ─────────────────────
def test_replace_model_everywhere(home):  # noqa: F811
    from myclaw import model_health as mh
    from myclaw.config import load_config, save_config

    cfg = load_config()
    cfg.agents.defaults.provider, cfg.agents.defaults.model = "gemini", "gone"
    named = type(cfg.agents).model_fields["named"].annotation.__args__[0]
    cfg.agents.named = [named(name="A", provider="gemini", model="gone"), named(name="B", provider="", model="gone"),
                        named(name="C", provider="openrouter", model="keep")]
    cfg.agents.rotation.entries = [_choice(cfg, "gemini", "gone"), _choice(cfg, "openrouter", "keep")]
    save_config(cfg)

    out = asyncio.run(mh.replace_model("gemini", "gone", "openrouter", "keep", reason="404"))
    assert set(out["changed"]) == {"default", "A", "B", "rotation"}
    cfg = load_config()
    assert (cfg.agents.defaults.provider, cfg.agents.defaults.model) == ("openrouter", "keep")
    assert {(a.name, a.provider, a.model) for a in cfg.agents.named} == {
        ("A", "openrouter", "keep"), ("B", "openrouter", "keep"), ("C", "openrouter", "keep")}
    assert [(e.provider, e.model) for e in cfg.agents.rotation.entries] == [("openrouter", "keep")]
    hist = mh.history()
    assert hist[0]["old"] == "gemini/gone" and hist[0]["new"] == "openrouter/keep" and "A" in hist[0]["changed"]


def test_permanent_failure_switches_agents_in_the_background(home, monkeypatch):  # noqa: F811
    from myclaw import model_health as mh
    from myclaw.config import save_config

    a, _ = _agent(home, monkeypatch, {("gemini", "cohere/north-mini-code:free"): NOT_FOUND})
    save_config(a.config)
    seen = []

    async def fake_replace(op, om, np_, nm, reason=""):
        seen.append((op, om, np_, nm))
        return {"changed": ["Coder"]}

    monkeypatch.setattr(mh, "replace_model", fake_replace)

    async def go():
        await a._rotating_chat(a._rotation_candidates(), MSG, False)
        await asyncio.sleep(0.05)  # the switch runs as a background task

    asyncio.run(go())
    assert seen and seen[0][:2] == ("gemini", "cohere/north-mini-code:free") and seen[0][2] == "openrouter"


def test_temporary_failures_never_replace(home, monkeypatch):  # noqa: F811
    from myclaw import model_health as mh

    called = []

    async def fake_replace(*a, **k):
        called.append(a)
        return {}

    monkeypatch.setattr(mh, "replace_model", fake_replace)
    for err in (RuntimeError("429 rate limit"), RuntimeError("connection refused"), RuntimeError("402 payment required")):
        mh.reset()
        a, _ = _agent(home, monkeypatch, {("gemini", "cohere/north-mini-code:free"): err})
        asyncio.run(a._rotating_chat(a._rotation_candidates(), MSG, False))
    assert not called
    assert mh.is_permanent(NOT_FOUND) and mh.is_permanent("only available on agentic harnesses")
    assert not mh.is_permanent("Error 429 rate limited") and not mh.is_permanent("timed out")


# ── provider/model mismatch ──────────────────────────────────────────────────
@pytest.mark.parametrize("provider,model,bad", [
    ("gemini", "cohere/north-mini-code:free", True),
    ("gemini-openai", "cohere/north-mini-code:free", True),
    ("gemini-openai", "models/gemini-3.5-flash", False),
    ("openrouter", "openrouter/openrouter/cohere/north-mini-code:free", True),
    ("openrouter", "openrouter/cohere/north-mini-code:free", True),
    ("openrouter", "gpt-4o", True),
    ("openrouter", "openai/gpt-4o", False),
    ("openrouter", "openrouter/free", False),
    ("openai", "gpt-4o", False),
    ("openai", "anthropic/claude-3", True),
    ("ollama", "llama3.2", False),
    ("ollama", "hf.co/user/model:Q4", False),
])
def test_mismatch(provider, model, bad):
    from myclaw.model_health import model_mismatch

    assert bool(model_mismatch(provider, model)) is bad


def test_doctor_lists_mismatched_agents(home):  # noqa: F811
    from myclaw.config import load_config, save_config
    from myclaw.doctor import run_checks

    cfg = load_config()
    cfg.agents.defaults.provider, cfg.agents.defaults.model = "gemini-openai", "models/gemini-3.5-flash"
    named = type(cfg.agents).model_fields["named"].annotation.__args__[0]
    cfg.agents.named = [named(name="Coder", provider="", model="cohere/north-mini-code:free")]
    save_config(cfg)
    r = {x.id: x for x in run_checks(offline=True, only=["model_mismatch"]).results}["model_mismatch"]
    assert r.status == "warn" and "Coder" in r.detail and "cohere/north-mini-code:free" in r.detail


# ── API ──────────────────────────────────────────────────────────────────────
def test_api_health_and_replace(home):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import model_health as mh
    from myclaw.config import load_config, save_config
    from myclaw.web import models_api

    cfg = load_config()
    named = type(cfg.agents).model_fields["named"].annotation.__args__[0]
    cfg.agents.named = [named(name="A", provider="openrouter", model="old")]
    save_config(cfg)
    mh.record("openrouter", "old", False, 2.0, "404 not found")
    app = FastAPI()
    app.include_router(models_api.router)
    with TestClient(app) as c:
        h = c.get("/api/models/health").json()
        assert h["models"][0]["model"] == "old" and h["models"][0]["failures"] == 1 and "history" in h
        r = c.post("/api/models/replace", json={"old_provider": "openrouter", "old_model": "old",
                                                 "new_provider": "openrouter", "new_model": "new"})
        assert r.status_code == 200 and "A" in r.json()["changed"]
        assert c.post("/api/models/replace", json={"old_provider": "x", "old_model": "y",
                                                   "new_provider": "nope", "new_model": "z"}).status_code == 400
    assert load_config().agents.named[0].model == "new"
    assert json.loads((home / ".myclaw" / "model_changes.jsonl").read_text().splitlines()[-1])["new"] == "openrouter/new"
