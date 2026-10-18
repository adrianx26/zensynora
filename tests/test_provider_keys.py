"""Several API keys per provider (v0.7.5): config, provider cache, agents, key
rotation, settings/keys API, probe with a key, create_agent with a key.

Everything runs offline: the OpenAI client and providers are faked.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def home(tmp_path, monkeypatch):
    import myclaw.config as cfg_mod
    import myclaw.provider as prov
    from myclaw import llm_errors
    from myclaw.web import settings_api

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_dir = tmp_path / ".myclaw"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(settings_api, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(llm_errors, "MODEL_COOLDOWN", {})
    cfg_mod._cached_config = None
    prov.clear_provider_cache()
    yield cfg_file
    cfg_mod._cached_config = None
    prov.clear_provider_cache()


def _write(cfg_file, data):
    cfg_file.write_text(json.dumps(data))
    import myclaw.config as cfg_mod

    cfg_mod._cached_config = None


BASE = {
    "providers": {"openrouter": {"api_key": "sk-main", "keys": [
        {"id": "work", "label": "Work account", "api_key": "sk-work"},
        {"id": "free2", "api_key": "sk-free2"}]}},
    "agents": {"defaults": {"provider": "openrouter", "model": "m1"}},
}


@pytest.fixture
def client(home):
    from myclaw.web import models_api, settings_api

    app = FastAPI()
    app.include_router(settings_api.router)
    app.include_router(models_api.router)
    return TestClient(app)


# ── Config ───────────────────────────────────────────────────────────────────
def test_old_configs_load_unchanged(home):
    from myclaw.config import load_config

    _write(home, {"providers": {"openrouter": {"api_key": "sk-old"}},
                  "agents": {"defaults": {"provider": "openrouter", "model": "x"}}})
    cfg = load_config(force_reload=True)
    assert cfg.providers.openrouter.keys == [] and cfg.providers.openrouter.rotate_keys is False
    assert cfg.agents.defaults.key == "" and cfg.providers.openrouter.api_key.get_secret_value() == "sk-old"


def test_key_ids_are_validated():
    from pydantic import ValidationError

    from myclaw.config import OpenRouterConfig

    with pytest.raises(ValidationError):
        OpenRouterConfig(keys=[{"id": "a", "api_key": "1"}, {"id": "a", "api_key": "2"}])
    with pytest.raises(ValidationError):
        OpenRouterConfig(keys=[{"id": "default", "api_key": "1"}])
    with pytest.raises(ValidationError):
        OpenRouterConfig(keys=[{"id": "Has Space", "api_key": "1"}])
    assert OpenRouterConfig(keys=[{"id": "Work", "api_key": "1"}]).keys[0].id == "work"


def test_save_config_keeps_secrets_inside_lists(home):
    from myclaw.config import load_config, save_config

    _write(home, {**BASE, "providers": {**BASE["providers"], "custom": [
        {"name": "deep", "base_url": "https://x/v1", "api_key": "sk-c", "keys": [{"id": "b", "api_key": "sk-cb"}]}]}})
    save_config(load_config(force_reload=True))  # an AppConfig, not a dict
    saved = json.loads(home.read_text())
    assert saved["providers"]["openrouter"]["keys"][0]["api_key"] == "sk-work"
    assert saved["providers"]["custom"][0]["keys"][0]["api_key"] == "sk-cb"


def test_keys_are_encrypted_at_rest(home, monkeypatch):
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    from myclaw import config_encryption as ce
    from myclaw.config import load_config, save_config

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(ce, "_load_key", lambda: key)
    monkeypatch.setattr(ce, "_get_or_create_key", lambda: key)
    save_config(BASE)
    text = home.read_text()
    assert "__encrypted__" in text and "sk-work" not in text  # whole file, list items included
    assert load_config(force_reload=True).providers.openrouter.keys[0].api_key.get_secret_value() == "sk-work"


# ── Settings API: masking, keep-on-empty ─────────────────────────────────────
def test_settings_masks_keys_and_keeps_them_on_empty(home, client):
    _write(home, BASE)
    body = client.get("/api/settings").json()
    assert "sk-work" not in json.dumps(body) and "sk-free2" not in json.dumps(body)
    assert body["config"]["providers"]["openrouter"]["keys"][0] == {"id": "work", "label": "Work account", "api_key": ""}
    assert "providers.openrouter.keys.work.api_key" in body["secrets_set"]

    # The form sends the list back with empty secrets: stored ones are kept.
    keys = body["config"]["providers"]["openrouter"]["keys"]
    keys[0]["label"] = "Job"
    r = client.put("/api/settings", json={"changes": {"providers.openrouter.keys": keys}})
    assert r.status_code == 200, r.text
    saved = json.loads(home.read_text())["providers"]["openrouter"]["keys"]
    assert saved[0] == {"id": "work", "label": "Job", "api_key": "sk-work"}
    assert saved[1]["api_key"] == "sk-free2"

    bad = client.put("/api/settings", json={"changes": {"providers.openrouter.keys": [{"id": "x"}, {"id": "x"}]}})
    assert bad.status_code == 400
    # An agent may not point at a key that does not exist.
    r = client.put("/api/settings", json={"changes": {"agents.defaults.key": "nope"}})
    assert r.status_code == 400 and "nope" in r.json()["detail"]
    assert client.put("/api/settings", json={"changes": {"agents.defaults.key": "work"}}).status_code == 200


def test_custom_provider_keys_survive_a_provider_edit(home, client):
    _write(home, {"providers": {"custom": [{"name": "deep", "base_url": "https://x/v1", "api_key": "sk-c",
                                            "keys": [{"id": "b", "label": "B", "api_key": "sk-cb"}]}]}})
    body = client.get("/api/settings").json()
    item = body["config"]["providers"]["custom"][0]
    assert item["keys"][0]["api_key"] == "" and "providers.custom.deep.keys.b.api_key" in body["secrets_set"]
    item["label"] = "DeepSeek"
    assert client.put("/api/settings", json={"changes": {"providers.custom": [item]}}).status_code == 200
    saved = json.loads(home.read_text())["providers"]["custom"][0]
    assert saved["keys"][0]["api_key"] == "sk-cb" and saved["api_key"] == "sk-c"


# ── Keys API: add / rename / delete / rotate / test ──────────────────────────
def test_keys_api_add_rename_delete(home, client):
    _write(home, BASE)
    r = client.post("/api/provider-keys/openrouter", json={"label": "Free tier", "api_key": " sk-new \n"})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == "free-tier"
    assert "sk-new" not in r.text
    keys = r.json()["providers"]["openrouter"]["keys"]
    assert [k["id"] for k in keys] == ["default", "work", "free2", "free-tier"]
    assert json.loads(home.read_text())["providers"]["openrouter"]["keys"][2]["api_key"] == "sk-new"
    # Same label again gets its own id.
    assert client.post("/api/provider-keys/openrouter", json={"label": "Free tier", "api_key": "k"}).json()["added"] == "free-tier-2"

    r = client.patch("/api/provider-keys/openrouter/work", json={"label": "Office"})
    assert r.status_code == 200
    assert next(k for k in r.json()["providers"]["openrouter"]["keys"] if k["id"] == "work")["label"] == "Office"
    assert json.loads(home.read_text())["providers"]["openrouter"]["keys"][0]["api_key"] == "sk-work"
    client.patch("/api/provider-keys/openrouter/work", json={"api_key": "sk-work2"})
    assert json.loads(home.read_text())["providers"]["openrouter"]["keys"][0]["api_key"] == "sk-work2"
    assert client.patch("/api/provider-keys/openrouter/default", json={"label": "x"}).status_code == 400

    # A key in use cannot be deleted; shows who uses it.
    client.put("/api/settings", json={"changes": {"agents.named": [
        {"name": "Coder", "provider": "openrouter", "model": "m", "key": "work"}]}})
    r = client.delete("/api/provider-keys/openrouter/work")
    assert r.status_code == 409 and "Coder" in r.json()["detail"]
    used = client.get("/api/provider-keys").json()["providers"]["openrouter"]["keys"]
    assert next(k for k in used if k["id"] == "work")["used_by"] == ["Coder"]
    assert next(k for k in used if k["id"] == "default")["used_by"] == ["default"]
    r = client.delete("/api/provider-keys/openrouter/free2")
    assert r.status_code == 200 and "free2" not in [k["id"] for k in r.json()["providers"]["openrouter"]["keys"]]
    assert client.delete("/api/provider-keys/openrouter/default").status_code == 400
    assert client.delete("/api/provider-keys/ollama/x").status_code == 404

    r = client.put("/api/provider-keys/openrouter/rotate", json={"enabled": True})
    assert r.json()["providers"]["openrouter"]["rotate_keys"] is True


def test_keys_api_on_a_custom_provider(home, client):
    _write(home, {"providers": {"custom": [{"name": "deep", "base_url": "https://x/v1", "api_key": "sk-c"}]}})
    r = client.post("/api/provider-keys/deep", json={"label": "Second", "api_key": "sk-2"})
    assert r.status_code == 200, r.text
    saved = json.loads(home.read_text())["providers"]["custom"][0]
    assert saved["keys"] == [{"id": "second", "label": "Second", "api_key": "sk-2"}] and saved["api_key"] == "sk-c"


def test_probe_and_model_list_use_the_chosen_key(home, client, monkeypatch):
    import httpx

    _write(home, BASE)
    seen = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            seen.append((headers or {}).get("Authorization"))
            return httpx.Response(200, json={"data": [{"id": "model-a"}]}, request=httpx.Request("GET", url))

    from myclaw.web import settings_api

    monkeypatch.setattr(settings_api.httpx, "AsyncClient", FakeClient)
    assert client.post("/api/provider-keys/openrouter/work/test").json()["models"] == ["model-a"]
    assert client.post("/api/settings/probe", json={"provider": "openrouter", "key_id": "free2"}).json()["ok"]
    assert client.get("/api/models/openrouter?key=work").json()["ok"]
    client.get("/api/models/openrouter")
    assert seen == ["Bearer sk-work", "Bearer sk-free2", "Bearer sk-work", "Bearer sk-main"]
    r = client.post("/api/settings/probe", json={"provider": "openrouter", "key_id": "gone"}).json()
    assert not r["ok"] and "gone" in r["error"]


def test_default_model_with_a_key(home, client):
    _write(home, BASE)
    r = client.post("/api/models/default", json={"provider": "openrouter", "model": "m2", "key": "work"})
    assert r.status_code == 200, r.text
    assert r.json()["default"] == {"provider": "openrouter", "model": "m2", "key": "work"}
    assert {"provider": "openrouter", "model": "m1"} in r.json()["rotation"]["entries"]
    assert client.post("/api/models/default", json={"provider": "openrouter", "model": "m2", "key": "zz"}).status_code == 400
    r = client.post("/api/models/rotate").json()
    assert r["default"] == {"provider": "openrouter", "model": "m1"}
    assert {"provider": "openrouter", "model": "m2", "key": "work"} in r["rotation"]["entries"]


# ── Provider cache per key ───────────────────────────────────────────────────
def test_provider_cache_is_per_key(home, monkeypatch):
    import openai

    import myclaw.provider as prov
    from myclaw.config import load_config

    _write(home, BASE)
    used = []

    class FakeOpenAI:
        def __init__(self, api_key=None, **kw):
            used.append(api_key)

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeOpenAI)
    cfg = load_config(force_reload=True)
    a = prov.get_provider(cfg, "openrouter")
    b = prov.get_provider(cfg, "openrouter", key_id="work")
    assert a is not b and used == ["sk-main", "sk-work"]
    assert prov.get_provider(cfg, "openrouter", key_id="work") is b  # cached
    assert prov.get_provider(cfg, "openrouter", key_id="default") is a
    assert ("openrouter", "work") in prov._provider_cache and "openrouter" in prov._provider_cache
    with pytest.raises(ValueError, match="no key 'gone'"):
        prov.get_provider(cfg, "openrouter", key_id="gone")
    # The settings object itself is not changed.
    assert cfg.providers.openrouter.api_key.get_secret_value() == "sk-main"


def test_custom_provider_key(home, monkeypatch):
    import openai

    import myclaw.provider as prov
    from myclaw.config import load_config

    _write(home, {"providers": {"custom": [{"name": "deep", "base_url": "https://x/v1", "api_key": "sk-c",
                                            "keys": [{"id": "b", "api_key": "sk-cb"}]}]}})
    used = []
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda api_key=None, **kw: used.append(api_key))
    cfg = load_config(force_reload=True)
    prov.get_provider(cfg, "deep", key_id="b")
    prov.get_provider(cfg, "deep")
    assert used == ["sk-cb", "sk-c"]


# ── Agents ───────────────────────────────────────────────────────────────────
def test_agent_uses_its_named_key(home, monkeypatch):
    import myclaw.agent as agent_mod
    from myclaw.config import load_config

    _write(home, {**BASE, "agents": {"defaults": {"provider": "openrouter", "model": "m1", "key": "free2"},
                                     "named": [{"name": "Coder", "provider": "openrouter", "model": "c", "key": "work"},
                                               {"name": "Helper", "model": "h"}]}})
    calls = []
    monkeypatch.setattr(agent_mod, "get_provider", lambda c, n, key_id=None: calls.append((n, key_id)) or object())
    cfg = load_config(force_reload=True)
    agent_mod.Agent(cfg, name="Coder", model="c", provider_name="openrouter", key="work").provider
    agent_mod.Agent(cfg, name="default").provider
    agent_mod.Agent(cfg, name="Helper", model="h").provider  # no own provider: inherits the default key
    agent_mod.Agent(cfg, name="Plain", model="p", provider_name="openrouter").provider
    assert calls == [("openrouter", "work"), ("openrouter", "free2"), ("openrouter", "free2"), ("openrouter", None)]


def test_unknown_key_falls_back_with_a_warning(home, monkeypatch, caplog):
    import myclaw.agent as agent_mod
    from myclaw.config import load_config
    from myclaw.provider_keys import missing_key_refs

    _write(home, {**BASE, "agents": {"defaults": {"provider": "openrouter", "model": "m1"},
                                     "named": [{"name": "X", "provider": "openrouter", "model": "m", "key": "gone"}]}})
    cfg = load_config(force_reload=True)
    calls = []
    monkeypatch.setattr(agent_mod, "get_provider", lambda c, n: calls.append(n) or object())
    with caplog.at_level("WARNING"):
        a = agent_mod.Agent(cfg, name="X", model="m", provider_name="openrouter", key="gone")
    assert a._key is None and calls == [] and a.provider is not None and calls == ["openrouter"]
    assert "Agent X uses key 'gone' of openrouter, which no longer exists" in caplog.text
    assert missing_key_refs(cfg) == ["Agent X uses key 'gone' of openrouter, which no longer exists"]


def test_doctor_warns_about_a_missing_key(home):
    from myclaw.config import load_config
    from myclaw.doctor import checks
    from myclaw.doctor.registry import WARN

    _write(home, {**BASE, "agents": {"defaults": {"provider": "openrouter", "model": "m1", "key": "gone"}}})
    cfg = load_config(force_reload=True)

    class Ctx:
        def get_config(self):
            return cfg

    status, msg, _hint = checks._provider_config(Ctx())
    assert status == WARN and "Agent default uses key 'gone'" in msg


class _Prov:
    def __init__(self, key, calls, fail_keys, error="Error code: 429 - rate limit exceeded"):
        self.key, self.calls, self.fail_keys, self.error = key, calls, fail_keys, error

    async def chat(self, messages, model, stream=False):
        self.calls.append((model, self.key))
        if self.key in self.fail_keys:
            raise RuntimeError(self.error)
        return f"ok from {self.key}", None


def test_key_rotation_on_429_within_the_same_model(home, monkeypatch):
    import myclaw.agent as agent_mod
    from myclaw.config import load_config

    data = json.loads(json.dumps(BASE))
    data["providers"]["openrouter"]["rotate_keys"] = True
    _write(home, data)
    cfg = load_config(force_reload=True)
    calls = []
    monkeypatch.setattr(agent_mod, "_MODEL_COOLDOWN", {})
    monkeypatch.setattr(agent_mod, "get_provider",
                        lambda c, n, key_id=None: _Prov(key_id, calls, {None, "work"}))
    a = agent_mod.Agent(cfg, name="default")
    out = asyncio.run(a._provider_chat([{"role": "user", "content": "hi"}], "m1"))
    assert out[0] == "ok from free2" and a.last_key_used == "free2" and a.last_model_used == ("openrouter", "m1")
    assert calls == [("m1", None), ("m1", "work"), ("m1", "free2")]
    # Cooldowns are per (provider, model, key): the next request goes straight to free2.
    assert ("openrouter", "m1") in agent_mod._MODEL_COOLDOWN
    assert ("openrouter", "m1", "work") in agent_mod._MODEL_COOLDOWN
    calls.clear()
    asyncio.run(a._provider_chat([{"role": "user", "content": "hi"}], "m1"))
    assert calls == [("m1", "free2")]


def test_no_key_rotation_when_off_or_for_other_errors(home, monkeypatch):
    import myclaw.agent as agent_mod
    from myclaw.agent import ProviderUnavailableError
    from myclaw.config import load_config

    data = json.loads(json.dumps(BASE))
    data["providers"]["openrouter"]["rotate_keys"] = True
    data["agents"]["rotation"] = {"enabled": True, "entries": [{"provider": "openrouter", "model": "m2", "key": "work"}]}
    _write(home, data)
    cfg = load_config(force_reload=True)
    calls = []
    monkeypatch.setattr(agent_mod, "_MODEL_COOLDOWN", {})
    # "model not found" is not a key problem: move on to the next model at once.
    monkeypatch.setattr(agent_mod, "get_provider", lambda c, n, key_id=None: _Prov(
        key_id, calls, {None} if not calls else set(), error="Error code: 404 - model not found"))
    a = agent_mod.Agent(cfg, name="default")
    assert a._rotation_candidates() == [("openrouter", "m1"), ("openrouter", "m2", "work")]
    out = asyncio.run(a._provider_chat([{"role": "user", "content": "hi"}], "m1"))
    assert out[0] == "ok from work" and calls == [("m1", None), ("m2", "work")]

    # rotate_keys off: a 429 is not retried with another key.
    data["providers"]["openrouter"]["rotate_keys"] = False
    data["agents"]["rotation"] = {"enabled": False, "entries": []}
    _write(home, data)
    cfg = load_config(force_reload=True)
    calls.clear()
    monkeypatch.setattr(agent_mod, "_MODEL_COOLDOWN", {})
    monkeypatch.setattr(agent_mod, "get_provider", lambda c, n, key_id=None: _Prov(key_id, calls, {None, "work", "free2"}))
    b = agent_mod.Agent(cfg, name="default")
    b._provider_breaker, b._offline_mode = None, False
    monkeypatch.setattr(agent_mod, "RATE_LIMIT_RETRY_SECONDS", 0)
    with pytest.raises(Exception) as exc:
        asyncio.run(b._provider_chat([{"role": "user", "content": "hi"}], "m1"))
    # 2026-10-18: a rate limit is retried once with the SAME key — never another one.
    assert not isinstance(exc.value, ProviderUnavailableError) and calls == [("m1", None), ("m1", None)]


# ── create_agent tool ────────────────────────────────────────────────────────
def test_create_agent_with_a_key(home, monkeypatch):
    from myclaw.agent_team import create_agent
    from myclaw.config import load_config

    _write(home, BASE)
    monkeypatch.setattr("myclaw.agent_skills.list_skills", lambda: [])
    out = create_agent("Worker", "Does work", key="work")
    assert out.startswith("Created agent @Worker"), out
    cfg = load_config(force_reload=True)
    assert next(a for a in cfg.agents.named if a.name == "Worker").key == "work"
    err = create_agent("Other", "x", key="nope")
    assert err.startswith("Error") and "nope" in err and "work" in err
    from myclaw.tools.core import _agent_registry

    assert _agent_registry["Worker"]._key == "work"
