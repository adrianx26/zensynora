"""Telegram bot inside the Web UI: status, token test, restart, startup/shutdown.

Everything is offline: httpx and the TelegramChannel class are faked.
"""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_v071_features import home  # noqa: F401  (fixture)

TOKEN = "123456:ABC-secret-token"


def _write_config(home_dir, **telegram):  # noqa: F811
    import myclaw.config as cfg_mod

    tg = {"enabled": True, "token": TOKEN, "allowFrom": ["12345"], "run_in_webui": True}
    tg.update(telegram)
    (home_dir / ".myclaw" / "config.json").write_text(json.dumps({"channels": {"telegram": tg}}))
    cfg_mod._cached_config = None


class FakeChannel:
    instances: list = []

    def __init__(self, config, registry):
        self.config = config
        self.registry = registry
        self.state = "stopped"
        self.started = 0
        self.stopped = 0
        FakeChannel.instances.append(self)

    async def start(self):
        self.started += 1
        self.state = "running"

    async def stop(self):
        self.stopped += 1
        self.state = "stopped"

    def status(self):
        return {"state": self.state, "bot_username": "zen_bot" if self.state == "running" else None,
                "error": None, "since": 1.0}


@pytest.fixture
def tg(home, monkeypatch):  # noqa: F811
    from myclaw.web import api as _web_api  # noqa: F401  (wires telegram_api into the Web UI)
    from myclaw.web import settings_api, telegram_api

    FakeChannel.instances = []
    monkeypatch.setattr(telegram_api, "_load_channel_class", lambda: FakeChannel)
    monkeypatch.setattr(telegram_api, "_channel", None)
    monkeypatch.setattr(telegram_api, "_fingerprint", None)
    monkeypatch.setattr(telegram_api, "_last_error", None)
    app = FastAPI()
    app.include_router(telegram_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None
    with TestClient(app) as c:
        yield c, telegram_api, home


# ── Status ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("allow, placeholder", [
    ([], True),
    (["YOUR_USER_ID"], True),
    (["12345", "@your_username"], True),
    (["12345", "@alice"], False),
])
def test_status_detects_placeholder_users(tg, allow, placeholder):
    c, _, home_dir = tg
    _write_config(home_dir, allowFrom=allow)
    body = c.get("/api/telegram/status").json()
    assert body["enabled"] is True and body["run_in_webui"] is True and body["token_set"] is True
    assert body["allow_from"] == allow
    assert body["allow_from_placeholder"] is placeholder
    assert body["bot"]["state"] == "stopped"
    assert TOKEN not in json.dumps(body)


# ── Test token ──────────────────────────────────────────────────────────────
class _FakeClient:
    def __init__(self, handler, seen):
        self.handler = handler
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.seen.append(url)
        return self.handler(url)


def _patch_httpx(monkeypatch, handler):
    from myclaw.web import telegram_api

    seen: list = []
    monkeypatch.setattr(telegram_api.httpx, "AsyncClient", lambda *a, **kw: _FakeClient(handler, seen))
    return seen


def test_token_test_ok_uses_unsaved_token(tg, monkeypatch):
    c, _, home_dir = tg
    _write_config(home_dir)
    seen = _patch_httpx(monkeypatch, lambda url: httpx.Response(
        200, json={"ok": True, "result": {"username": "zen_bot", "first_name": "Zen"}}))
    r = c.post("/api/telegram/test", json={"token": "999:typed"}).json()
    assert r == {"ok": True, "bot_username": "zen_bot", "bot_name": "Zen"}
    assert seen == ["https://api.telegram.org/bot999:typed/getMe"]
    c.post("/api/telegram/test", json={})  # falls back to the saved token
    assert seen[-1] == f"https://api.telegram.org/bot{TOKEN}/getMe"


def test_token_test_wrong_token(tg, monkeypatch):
    c, _, home_dir = tg
    _write_config(home_dir)
    _patch_httpx(monkeypatch, lambda url: httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    r = c.post("/api/telegram/test", json={}).json()
    assert r["ok"] is False and "token is wrong" in r["error"] and "@BotFather" in r["error"]
    assert TOKEN not in json.dumps(r)


def test_token_test_network_error(tg, monkeypatch):
    c, _, home_dir = tg
    _write_config(home_dir)

    def boom(url):
        raise httpx.ConnectError(f"failed for {url}")

    _patch_httpx(monkeypatch, boom)
    r = c.post("/api/telegram/test", json={}).json()
    assert r["ok"] is False and "Cannot reach Telegram" in r["error"]
    assert TOKEN not in json.dumps(r)


def test_token_test_without_any_token(tg):
    c, _, home_dir = tg
    _write_config(home_dir, token="")
    r = c.post("/api/telegram/test", json={}).json()
    assert r["ok"] is False and "@BotFather" in r["error"]


# ── Restart ─────────────────────────────────────────────────────────────────
def test_restart_refused_when_disabled_or_not_in_webui(tg):
    c, _, home_dir = tg
    _write_config(home_dir, enabled=False)
    r = c.post("/api/telegram/restart")
    assert r.status_code == 409 and "turned off" in r.json()["detail"]
    _write_config(home_dir, run_in_webui=False)
    r = c.post("/api/telegram/restart")
    assert r.status_code == 409 and "gateway" in r.json()["detail"]
    assert FakeChannel.instances == []


def test_restart_starts_a_new_bot(tg):
    c, mod, home_dir = tg
    _write_config(home_dir)
    r = c.post("/api/telegram/restart")
    assert r.status_code == 200 and r.json()["bot"]["state"] == "running"
    r = c.post("/api/telegram/restart")
    assert len(FakeChannel.instances) == 2
    assert FakeChannel.instances[0].stopped == 1 and FakeChannel.instances[1].state == "running"
    assert c.get("/api/telegram/status").json()["bot"]["bot_username"] == "zen_bot"


# ── Lifecycle in the Web UI process ─────────────────────────────────────────
def test_webui_startup_and_shutdown_run_the_bot(tg):
    _, mod, home_dir = tg
    from myclaw.web import api as web_api

    _write_config(home_dir)
    getter_seen = []

    async def run():
        await web_api._start_background_runner()
        ch = mod._channel
        getter_seen.append(ch.registry)
        assert ch is not None and ch.state == "running"
        assert mod.bot_state() == "running"
        await web_api._stop_background_runner()
        return ch

    ch = asyncio.run(run())
    assert ch.started == 1 and ch.stopped == 1 and mod._channel is None
    # the bot gets the registry *getter*, so it always sees the current agents
    assert getter_seen[0] is web_api._ensure_registry


def test_webui_startup_skips_bot_when_not_wanted(tg):
    _, mod, home_dir = tg
    from myclaw.web import api as web_api

    for kw in ({"enabled": False}, {"run_in_webui": False}, {"token": ""}):
        _write_config(home_dir, **kw)

        async def run():
            await web_api._start_background_runner()
            await web_api._stop_background_runner()

        asyncio.run(run())
    assert FakeChannel.instances == []


def test_bot_that_fails_to_start_does_not_break_the_webui(tg, monkeypatch):
    _, mod, home_dir = tg
    from myclaw.web import api as web_api

    class Broken(FakeChannel):
        async def start(self):
            raise RuntimeError(f"bad token {TOKEN}")

    monkeypatch.setattr(mod, "_load_channel_class", lambda: Broken)
    _write_config(home_dir)

    async def run():
        await web_api._start_background_runner()  # must not raise
        st = mod.bot_status()
        await web_api._stop_background_runner()
        return st

    st = asyncio.run(run())
    assert st["state"] == "error" and TOKEN not in st["error"]


def test_settings_save_restarts_only_on_telegram_changes(tg):
    _, mod, home_dir = tg
    _write_config(home_dir)

    async def run():
        await mod.start_bot()
        await mod.on_settings_changed()          # nothing changed: same bot
        assert len(FakeChannel.instances) == 1
        _write_config(home_dir, allowFrom=["12345", "@bob"])
        await mod.on_settings_changed()          # Telegram changed: restarted
        assert len(FakeChannel.instances) == 2 and FakeChannel.instances[0].stopped == 1
        _write_config(home_dir, enabled=False)
        await mod.on_settings_changed()          # turned off: stopped
        assert mod._channel is None and FakeChannel.instances[1].stopped == 1
        _write_config(home_dir)
        await mod.on_settings_changed()          # turned on again: started
        assert mod._channel is FakeChannel.instances[2]
        await mod.stop_bot()

    asyncio.run(run())


def test_health_reports_bot_state(tg, monkeypatch):
    _, mod, home_dir = tg
    from myclaw.web import settings_api

    _write_config(home_dir)
    assert settings_api._telegram_bot_state() == "stopped"
    asyncio.run(mod.start_bot())
    assert settings_api._telegram_bot_state() == "running @zen_bot"
    asyncio.run(mod.stop_bot())


def test_telegram_changes_need_no_restart_when_in_webui(tg, monkeypatch):
    _, mod, home_dir = tg
    from myclaw.web import settings_api

    _write_config(home_dir)
    monkeypatch.setattr(settings_api, "_on_config_changed", None)
    app = FastAPI()
    app.include_router(settings_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None
    c = TestClient(app)
    r = c.put("/api/settings", json={"changes": {"channels.telegram.allowFrom": ["777"],
                                                 "channels.whatsapp.enabled": True}})
    assert r.status_code == 200, r.text
    assert r.json()["restart_needed_for"] == ["channels.whatsapp.enabled"]


# ── 2026-10-18: a token pasted with a line break; "Send test message" ──────────
def test_token_with_spaces_or_line_break_is_trimmed(home):  # noqa: F811
    """Real case: Test bot (which trimmed) said OK, the bot said 'token is wrong'."""
    from myclaw.channels.telegram import TelegramChannel
    from myclaw.config import load_config

    _write_config(home, token=" 123456:ABC-secret-token\n")
    ch = TelegramChannel(load_config(force_reload=True), {"default": None})
    assert ch.token == "123456:ABC-secret-token"


def test_saving_settings_trims_secret_values(home):  # noqa: F811
    from myclaw.config import load_config
    from myclaw.web import settings_api

    _write_config(home)
    asyncio.run(settings_api.update_settings(settings_api.SettingsUpdate(
        changes={"channels.telegram.token": "  999:NEW-token \n"})))
    assert load_config(force_reload=True).channels.telegram.token.get_secret_value() == "999:NEW-token"


def test_send_test_message_to_allowed_users(tg):
    c, telegram_api, home_dir = tg
    _write_config(home_dir, allowFrom=["8008359361", "@alice", "777"])
    sent = []

    async def send_text(chat_id, text):
        if chat_id == "777":
            raise RuntimeError("Forbidden: bot can't initiate conversation with a user")
        sent.append((chat_id, text))

    assert c.post("/api/telegram/send-test", json={}).status_code == 409        # bot not running
    asyncio.run(telegram_api.start_bot())
    telegram_api._channel.send_text = send_text
    r = c.post("/api/telegram/send-test", json={}).json()
    assert [x["chat_id"] for x in r["results"]] == ["8008359361", "777"]     # @usernames skipped
    assert sent[0][0] == "8008359361" and "ZenSynora is connected" in sent[0][1]
    assert r["ok"] is False and "press Start" in r["results"][1]["error"]
    assert c.post("/api/telegram/send-test", json={"chat_id": "8008359361"}).json()["ok"] is True
    asyncio.run(telegram_api.stop_bot())
