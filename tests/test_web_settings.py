"""Web UI settings API: read/update config, secret handling, access guard."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import myclaw.config as cfg_mod
    from myclaw.web import settings_api

    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(settings_api, "CONFIG_FILE", cfg_file)
    cfg_mod._cached_config = None

    app = FastAPI()
    app.include_router(settings_api.router)
    yield TestClient(app), cfg_file
    cfg_mod._cached_config = None


def test_get_settings_masks_secrets(client):
    c, _ = client
    r = c.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert "ollama" in body["providers"]
    assert body["config"]["providers"]["openai"]["api_key"] == ""
    assert "providers.openai.api_key" in body["secret_paths"]


def test_update_writes_config_and_keeps_secret_when_blank(client):
    c, cfg_file = client
    r = c.put("/api/settings", json={"changes": {
        "agents.defaults.model": "qwen3",
        "providers.openai.api_key": "sk-abc",
    }})
    assert r.status_code == 200, r.text
    saved = json.loads(cfg_file.read_text())
    assert saved["agents"]["defaults"]["model"] == "qwen3"
    assert saved["providers"]["openai"]["api_key"] == "sk-abc"

    # Empty secret in a later save means "keep".
    c.put("/api/settings", json={"changes": {"providers.openai.api_key": ""}})
    assert json.loads(cfg_file.read_text())["providers"]["openai"]["api_key"] == "sk-abc"
    assert "providers.openai.api_key" in c.get("/api/settings").json()["secrets_set"]

    # Explicit clear removes it.
    c.put("/api/settings", json={"changes": {}, "clear_secrets": ["providers.openai.api_key"]})
    assert json.loads(cfg_file.read_text())["providers"]["openai"]["api_key"] == ""


def test_rejects_unknown_paths_and_providers(client):
    c, _ = client
    assert c.put("/api/settings", json={"changes": {"nope.x": 1}}).status_code == 400
    assert c.put("/api/settings", json={"changes": {"agents.defaults.provider": "bogus"}}).status_code == 400
    bad_agent = {"agents.named": [{"name": "default", "model": "m"}]}
    assert c.put("/api/settings", json={"changes": bad_agent}).status_code == 400


def test_remote_clients_are_refused(client):
    from myclaw.web import settings_api

    c, _ = client
    app = c.app
    remote = TestClient(app, client=("192.168.1.50", 5000))
    assert remote.get("/api/settings").status_code == 403
    assert settings_api  # imported for clarity
