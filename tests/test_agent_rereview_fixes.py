"""2026-10-28: fixes from the second review of agent fusion / settings saves."""

import asyncio
import json
import os
import stat
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from myclaw.web.agents_api import _ORIGIN as WEB
from tests.test_agent_levels_fusion import AGENTS, _write_config
from tests.test_agent_review_fixes import snap, team  # noqa: F401  (fixture)
from tests.test_v071_features import home  # noqa: F401  (fixture)


# ── 1. crash right after the settings were saved ─────────────────────────────
def test_a_crash_right_after_the_settings_save_is_undone(team, monkeypatch):  # noqa: F811
    from myclaw import agent_fusion as F
    import myclaw.config as cm

    before = snap(team)
    p = F.create_fusion("Writer", "Editor", "Combined", use_model=False)
    real = cm.save_config

    def save_then_die(raw):
        real(raw)
        raise SystemExit("killed right after the save")

    monkeypatch.setattr(cm, "save_config", save_then_die)
    with pytest.raises(SystemExit):
        F.apply_fusion(p["id"], WEB)
    monkeypatch.setattr(cm, "save_config", real)
    assert F.get_backup(p["id"])["config_saved"] is True
    assert "Editor" in [a["name"] for a in F.get_backup(p["id"])["agents_before"]["named"]]
    F.undo_fusion(p["id"], WEB)
    assert snap(team) == before


def test_restore_is_harmless_when_the_save_never_happened(team, monkeypatch):  # noqa: F811
    from myclaw import agent_fusion as F
    import myclaw.config as cm

    before = snap(team)
    p = F.create_fusion("Writer", "Editor", use_model=False)

    real = cm.save_config

    def die(raw):
        raise SystemExit("killed before the save")

    monkeypatch.setattr(cm, "save_config", die)
    with pytest.raises(SystemExit):
        F.apply_fusion(p["id"], WEB)
    monkeypatch.setattr(cm, "save_config", real)
    assert F.get_backup(p["id"])["config_saved"] is True  # recorded before the save
    F.undo_fusion(p["id"], WEB)
    assert snap(team) == before


# ── 2. atomic settings writes ────────────────────────────────────────────────
def test_settings_writes_are_atomic_and_private(home):  # noqa: F811
    import myclaw.config as cm

    cm.save_config({"agents": {"defaults": {"model": "m0"},
                               "named": [{"name": f"N{i}", "model": "x" * 300} for i in range(30)]}})
    assert stat.S_IMODE(os.stat(cm.CONFIG_FILE).st_mode) == 0o600
    errors, stop = [], time.time() + 1.5

    def saver():
        i = 0
        while time.time() < stop:
            cm.save_config({"agents": {"defaults": {"model": f"m{i}"},
                                       "named": [{"name": f"N{k}", "model": "x" * 300} for k in range(30)]}})
            i += 1

    def reader():
        while time.time() < stop:
            try:
                json.loads(cm.CONFIG_FILE.read_text())
                cm.load_config(force_reload=True)
            except BaseException as e:  # SystemExit for a half-written file
                errors.append(repr(e))

    ts = [threading.Thread(target=saver), threading.Thread(target=reader), threading.Thread(target=reader)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    assert not [f for f in os.listdir(cm.CONFIG_FILE.parent) if f.endswith(".tmp")]


def test_a_damaged_settings_file_is_replaced_by_the_last_good_copy(home):  # noqa: F811
    import myclaw.config as cm

    _write_config(home, [{"name": "Writer"}])
    cm.save_config({"agents": {"named": [{"name": "Writer"}, {"name": "Editor"}]}})  # keeps a last-good copy
    cm.CONFIG_FILE.write_text('{"agents": {"named": [{"na')  # a crash in the middle of an old-style write
    cm._cached_config = None
    cfg = cm.load_config(force_reload=True)
    assert [a.name for a in cfg.agents.named] == ["Writer"]  # the copy from before the last save
    assert any(f.startswith("config.json.broken-") for f in os.listdir(cm.CONFIG_FILE.parent))


# ── 3. interrupted fusions keep their safety checks ──────────────────────────
def _interrupt(F, store, monkeypatch, fid):
    orig, n = store.move_member, {"k": 0}

    def dying(*a, **k):
        n["k"] += 1
        if n["k"] == 2:
            raise SystemExit("die")
        return orig(*a, **k)

    monkeypatch.setattr(store, "move_member", dying)
    with pytest.raises(SystemExit):
        F.apply_fusion(fid, WEB)
    monkeypatch.setattr(store, "move_member", orig)


def test_interrupted_undo_refuses_when_a_removed_name_was_reused(team, monkeypatch):  # noqa: F811
    from myclaw import agent_fusion as F
    from myclaw.chat import store
    from myclaw.config import CONFIG_FILE, load_encrypted_or_plain, save_config
    import myclaw.config as cm

    p = F.create_fusion("Writer", "Editor", "Combined", use_model=False)
    _interrupt(F, store, monkeypatch, p["id"])
    raw = load_encrypted_or_plain(CONFIG_FILE)
    raw["agents"]["named"].append({"name": "Writer", "model": "BRAND-NEW"})
    save_config(raw)
    cm._cached_config = None
    with pytest.raises(F.FusionError, match="exists again"):
        F.undo_fusion(p["id"], WEB)
    names = [x["name"] for x in load_encrypted_or_plain(CONFIG_FILE)["agents"]["named"]]
    assert "Combined" in names and "Writer" in names  # nothing lost


def test_no_new_fusion_while_one_is_interrupted(team, monkeypatch):  # noqa: F811
    from myclaw import agent_fusion as F
    from myclaw.chat import store

    before = snap(team)
    p = F.create_fusion("Writer", "Editor", "Combined", use_model=False)
    other = F.create_fusion("Other", "Writer", use_model=False)
    _interrupt(F, store, monkeypatch, p["id"])
    with pytest.raises(F.FusionError, match="interrupted"):
        F.create_fusion("Combined", "Other", use_model=False)
    with pytest.raises(F.FusionError, match="interrupted"):
        F.apply_fusion(other["id"], WEB)
    F.undo_fusion(p["id"], WEB)
    assert snap(team) == before


# ── 4. member order ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("new", ["", "Combined"])
def test_undo_restores_the_exact_member_order(team, new):  # noqa: F811
    from myclaw import agent_fusion as F
    from myclaw.chat import store

    store.create_channel("x1", ["Writer", "Other", "Editor"], channel_id="x1")
    store.create_channel("x2", ["Editor", "Other", "Writer"], channel_id="x2")
    store.create_channel("x3", ["Other", "Editor", "default", "Writer"], channel_id="x3")
    before = snap(team)
    p = F.create_fusion("Writer", "Editor", new, use_model=False)
    F.apply_fusion(p["id"], WEB)
    F.undo_fusion(p["id"], WEB)
    assert snap(team) == before


# ── 5. an agent cannot become the medic ──────────────────────────────────────
def test_create_agent_cannot_make_a_medic(team):  # noqa: F811
    from myclaw import agent_fusion as F
    from myclaw.agent_team import create_agent
    from myclaw.config import CONFIG_FILE, load_encrypted_or_plain, save_config
    from myclaw.events import CURRENT_AGENT
    import myclaw.config as cm

    tok = CURRENT_AGENT.set("Writer")
    try:
        for name in ("medic", "Medic", "MEDIC"):
            assert create_agent(name, "heals").startswith("Error") and "reserved" in create_agent(name, "heals")
        assert create_agent("Helper", "helps").startswith("Created")
    finally:
        CURRENT_AGENT.reset(tok)
    # Even renamed to "medic" in the settings file, an agent-made agent is not the medic.
    raw = load_encrypted_or_plain(CONFIG_FILE)
    helper = next(a for a in raw["agents"]["named"] if a["name"] == "Helper")
    assert helper["created_by"] == "agent:Writer"
    helper["name"] = "medic"
    save_config(raw)
    cm._cached_config = None
    tok = CURRENT_AGENT.set("medic")
    try:
        assert asyncio.run(F.propose_agent_fusion("Writer", "Editor", reason="x")).startswith("Error: only the medic")
    finally:
        CURRENT_AGENT.reset(tok)
    helper["created_by"] = "you"  # a medic the user made
    save_config(raw)
    cm._cached_config = None
    tok = CURRENT_AGENT.set("medic")
    try:
        assert asyncio.run(F.propose_agent_fusion("Writer", "Editor", reason="x")).startswith("Fusion proposal")
    finally:
        CURRENT_AGENT.reset(tok)


# ── 6. strict levels in the settings API ─────────────────────────────────────
def test_settings_store_only_real_int_levels(home):  # noqa: F811
    import myclaw.config as cm
    from myclaw.web import settings_api

    _write_config(home, [dict(AGENTS[0])])
    app = FastAPI()
    app.include_router(settings_api.router)
    c = TestClient(app)
    for bad in (True, "5", 5.0, 12, 0, None, "high"):
        assert c.put("/api/settings", json={"changes": {"agents.defaults.level": bad}}).status_code == 422, bad
    assert c.put("/api/settings", json={"changes": {"agents.defaults.level": 5}}).status_code == 200
    stored = json.loads(cm.CONFIG_FILE.read_text())["agents"]["defaults"]["level"]
    assert stored == 5 and type(stored) is int
