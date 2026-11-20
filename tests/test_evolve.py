"""2026-11-03: Evolve — agent levels, agent fusion, AI change requests.

* Every agent has a level 1-9 (default 1; a label for now).
* Fusion merges two named agents into one: the model drafts name, description
  and prompt (you review and edit), skills are combined, the model comes from
  the agent you pick, the level is the lower one. Channels, direct messages
  and jobs move to the new agent; the two originals go to the Graveyard and
  can be revived by Medic.
* A change request becomes a one-off job with a precise prompt: the agent
  reads the code and files a change proposal, which is installed only after
  you approve it (existing self-update pipeline).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_v071_features import home  # noqa: F401  (fixture)


def _agents(**named):
    from myclaw.config import NamedAgentConfig, load_config, save_config

    cfg = load_config()
    cfg.agents.named = [NamedAgentConfig(name=n, **kw) for n, kw in named.items()]
    save_config(cfg)
    return cfg


def test_levels_default_and_bounds():
    from pydantic import ValidationError

    from myclaw.config import AgentDefaults, NamedAgentConfig

    assert NamedAgentConfig(name="x").level == 1 and AgentDefaults().level == 1
    for bad in (0, 10):
        with pytest.raises(ValidationError):
            NamedAgentConfig(name="x", level=bad)


def test_set_level(home):  # noqa: F811
    from myclaw import evolve
    from myclaw.config import load_config

    _agents(Coder={"model": "m"})
    asyncio.run(evolve.set_level("Coder", 4))
    asyncio.run(evolve.set_level("default", 2))
    cfg = load_config(force_reload=True)
    assert cfg.agents.named[0].level == 4 and cfg.agents.defaults.level == 2
    with pytest.raises(ValueError):
        asyncio.run(evolve.set_level("Nobody", 3))


def _fake(monkeypatch, answer, seen=None):
    async def fake(config, messages, provider=None, model=None):
        if seen is not None:
            seen.append(messages)
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer) if not isinstance(answer, str) else answer, "p", "m"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)


def test_fusion_preview_then_apply(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.chat import store as chat
    from myclaw.config import load_config
    from myclaw.jobs import store as jobs

    _agents(Coder={"model": "m1", "provider": "openrouter", "level": 5, "skills": ["git"], "system_prompt": "You write code."},
            Tester={"model": "m2", "level": 3, "skills": ["pytest", "git"], "system_prompt": "You test code."},
            Other={"model": "m3"})
    ch = chat.create_channel("team", ["Coder", "Tester", "Other"])
    job = jobs.create_job({"name": "nightly", "prompt": "p", "agent": "Tester"})
    seen: list = []
    _fake(monkeypatch, {"name": "Builder", "description": "Writes and tests code.",
                        "system_prompt": "You write code and test it."}, seen)

    p = asyncio.run(evolve.fusion_preview("Coder", "Tester"))
    assert p["name"] == "Builder" and p["level"] == 3 and p["skills"] == ["git", "pytest"]
    assert p["model_from"] == "Coder" and "You write code." in seen[0][1]["content"]
    assert len(load_config(force_reload=True).agents.named) == 3        # preview changes nothing

    out = asyncio.run(evolve.fuse("Coder", "Tester", name="Builder", description=p["description"],
                                  system_prompt="Edited prompt.", model_from="Tester"))
    cfg = load_config(force_reload=True)
    names = [a.name for a in cfg.agents.named]
    assert names == ["Other", "Builder"] and out["agent"]["name"] == "Builder"
    b = cfg.agents.named[1]
    assert (b.model, b.provider, b.level, b.skills, b.system_prompt) == ("m2", "", 3, ["git", "pytest"], "Edited prompt.")
    assert chat.get_channel(ch["id"])["members"] == ["Builder", "Other"]
    assert jobs.get_job(job["id"])["agent"] == "Builder"
    trash = evolve.agent_trash()
    assert {t["agent"]["name"] for t in trash} == {"Coder", "Tester"} and all(t["fused_into"] == "Builder" for t in trash)
    assert all(t["llm_disabled"] for t in trash)
    assert all(set(t["agent"]) == {"name", "level"} and "snapshot" not in t for t in trash)

    from myclaw.agents.medic_agent import MedicAgent

    revived = asyncio.run(MedicAgent().revive_agent("Coder", mode="active"))
    cfg = load_config(force_reload=True)
    assert [a.name for a in cfg.agents.named] == ["Other", "Builder", "Coder"]
    assert cfg.agents.named[2].level == 5 and revived["revived_by"] == "medic"
    assert {t["agent"]["name"] for t in evolve.agent_trash()} == {"Tester"}


@pytest.mark.parametrize("a,b,name", [("Coder", "Coder", "X"), ("Coder", "Nope", "X"), ("default", "Coder", "X"),
                                      ("Coder", "Tester", "default"), ("Coder", "Tester", "Other"), ("Coder", "Tester", " ")])
def test_fusion_rejects_bad_input(home, a, b, name):  # noqa: F811
    from myclaw import evolve

    _agents(Coder={"model": "m1"}, Tester={"model": "m2"}, Other={"model": "m3"})
    with pytest.raises(ValueError):
        asyncio.run(evolve.fuse(a, b, name=name, description="", system_prompt="p", model_from=a))


def test_fusion_preview_without_a_usable_answer_still_works(home, monkeypatch):  # noqa: F811
    from myclaw import evolve

    _agents(Coder={"model": "m1", "system_prompt": "A"}, Tester={"model": "m2", "system_prompt": "B"})
    _fake(monkeypatch, RuntimeError("429 rate limited"))
    p = asyncio.run(evolve.fusion_preview("Coder", "Tester"))
    assert p["name"] == "Coder-Tester" and "A" in p["system_prompt"] and "B" in p["system_prompt"]
    assert "rate" in p["note"]


def test_mindy_and_medic_cannot_be_fused(home):  # noqa: F811
    from myclaw import evolve

    _agents(Mindy={"model": "m1"}, Medic={"model": "m2"})
    with pytest.raises(ValueError, match="cannot be fused"):
        asyncio.run(evolve.fusion_preview("Mindy", "Medic"))
    with pytest.raises(ValueError, match="cannot be fused"):
        asyncio.run(evolve.fuse("Mindy", "Medic", name="Mindy and Medic v.1", description="", system_prompt="p", model_from="Mindy"))


def test_fusion_lineage_role_names_and_repeated_fusions(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.config import load_config

    _agents(Mindy={}, Medic={}, Coder={}, Tester={})
    _fake(monkeypatch, {"name": "unrelated", "system_prompt": "p"})
    assert asyncio.run(evolve.fusion_preview("Mindy", "Coder"))["name"] == "Mindy v.1"
    first = asyncio.run(evolve.fuse("Mindy", "Coder", "unrelated", "", "p", "Mindy"))
    assert first["agent"]["name"] == "Mindy v.1"
    with pytest.raises(ValueError, match="cannot be fused"):
        asyncio.run(evolve.fuse("Mindy v.1", "Medic", "X", "", "p", "Medic"))
    candidate = evolve.evaluate_fusion_candidates(50)["candidates"]
    assert not next(c for c in candidate if {c["a"], c["b"]} == {"Mindy v.1", "Medic"})["can_fuse"]
    second = asyncio.run(evolve.fuse("Mindy v.1", "Tester", "another", "", "p", "Tester"))
    assert second["agent"]["name"] == "Mindy v.2"
    assert {a.name for a in load_config(force_reload=True).agents.named} == {"Medic", "Mindy v.2"}
    with pytest.raises(ValueError, match="cannot be fused"):
        asyncio.run(evolve.fusion_preview("Mindy v.2", "Medic"))


def test_no_source_name_or_legacy_restore(home):  # noqa: F811
    from fastapi import HTTPException

    from myclaw import evolve
    from myclaw.config import load_config
    from myclaw.web import settings_api

    _agents(Coder={}, Tester={})
    with pytest.raises(ValueError, match="already in use"):
        asyncio.run(evolve.fuse("Coder", "Tester", "coder", "", "p", "Coder"))
    asyncio.run(evolve.fuse("Coder", "Tester", "Builder", "", "p", "Coder"))
    for call in (evolve.restore_agent("Coder"), evolve.revive_agent("Coder", by="medic")):
        with pytest.raises(ValueError, match="guarded"):
            asyncio.run(call)
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(settings_api.update_settings(settings_api.SettingsUpdate(changes={
            "agents.named": [{"name": "Coder"}, {"name": "Tester"}]
        })))
    assert conflict.value.status_code == 409
    assert all(a.name != "Coder" for a in load_config(force_reload=True).agents.named)


def test_fusion_saves_snapshots_before_config_and_reports_failures(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.config import load_config

    _agents(Coder={"system_prompt": "private coder", "key": ""}, Tester={"system_prompt": "private tester"})
    original_save = evolve._asave

    async def fail_after_save(changes):
        stored = json.loads(evolve._trash_file().read_text(encoding="utf-8"))
        assert {t["snapshot"]["name"] for t in stored if "snapshot" in t} == {"Coder", "Tester"}
        await original_save(changes)
        raise RuntimeError("reload failed after save")

    monkeypatch.setattr(evolve, "_asave", fail_after_save)
    with pytest.raises(evolve.LifecycleError, match="may have changed"):
        asyncio.run(evolve.fuse("Coder", "Tester", "Builder", "", "p", "Coder"))
    assert [a.name for a in load_config(force_reload=True).agents.named] == ["Builder"]
    assert {t["agent"]["name"] for t in evolve.agent_trash()} == {"Coder", "Tester"}


def test_snapshot_write_failure_leaves_agents_untouched(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.config import load_config

    _agents(Coder={}, Tester={})
    def fail(_):
        raise OSError("disk full")
    monkeypatch.setattr(evolve, "_write_trash", fail)
    with pytest.raises(evolve.LifecycleError, match="disk full"):
        asyncio.run(evolve.fuse("Coder", "Tester", "Builder", "", "p", "Coder"))
    assert [a.name for a in load_config(force_reload=True).agents.named] == ["Coder", "Tester"]


def test_failed_settings_save_can_abort_staged_fusion(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.config import load_config

    _agents(A={}, B={})
    original = evolve._asave

    async def fail(_):
        raise ValueError("invalid settings")

    monkeypatch.setattr(evolve, "_asave", fail)
    with pytest.raises(evolve.LifecycleError):
        asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    operation = evolve.pending_fusions()[0]["id"]
    assert asyncio.run(evolve.resume_fusion(operation))["state"] == "aborted"
    assert [a.name for a in load_config(force_reload=True).agents.named] == ["A", "B"]
    assert not evolve.agent_trash()
    monkeypatch.setattr(evolve, "_asave", original)
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))


def test_reference_migration_failure_is_reported_with_snapshots(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.config import load_config
    from myclaw.jobs import store as jobs
    from myclaw.jobs.runner import JobRunner

    _agents(Coder={"system_prompt": "saved"}, Tester={})
    a = jobs.create_job({"name": "a", "prompt": "p", "agent": "Coder"})
    b = jobs.create_job({"name": "b", "prompt": "p", "agent": "Tester"})
    real = jobs.rename_agent

    def fail(old, new):
        if old == "Tester":
            raise OSError("jobs unavailable")
        return real(old, new)

    monkeypatch.setattr(jobs, "rename_agent", fail)
    with pytest.raises(evolve.LifecycleError, match="resume"):
        asyncio.run(evolve.fuse("Coder", "Tester", "Builder", "", "p", "Coder"))
    assert [a.name for a in load_config(force_reload=True).agents.named] == ["Builder"]
    assert {t["agent"]["name"] for t in evolve.agent_trash()} == {"Coder", "Tester"}
    assert jobs.get_job(a["id"])["agent"] == "Builder" and jobs.get_job(b["id"])["agent"] == "Tester"
    operation = evolve.pending_fusions()[0]["id"]
    run_id = asyncio.run(JobRunner(lambda: {"default": object()}).run_job(b["id"]))
    run = jobs.get_run(run_id)
    assert run["status"] == "blocked" and "fusion needs recovery" in run["error"]
    assert jobs.get_job(b["id"])["last_status"] == "blocked"
    monkeypatch.setattr(jobs, "rename_agent", real)
    assert asyncio.run(evolve.resume_fusion(operation))["state"] == "complete"
    assert jobs.get_job(b["id"])["agent"] == "Builder" and not evolve.pending_fusions()


def test_concurrent_lifecycle_operation_fails_without_lost_update(home, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from myclaw import evolve
    from myclaw.config import load_config
    from myclaw.web import settings_api

    _agents(A={}, B={}, C={})
    original = evolve._asave

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused_save(changes):
            entered.set()
            await release.wait()
            await original(changes)

        monkeypatch.setattr(evolve, "_asave", paused_save)
        first = asyncio.create_task(evolve.fuse("A", "B", "Merged", "", "p", "A"))
        await entered.wait()
        with pytest.raises(evolve.LifecycleError, match="running"):
            await evolve.set_level("C", 4)
        with pytest.raises(HTTPException) as conflict:
            await settings_api.update_settings(settings_api.SettingsUpdate(changes={"agents.named": []}))
        assert conflict.value.status_code == 409
        release.set()
        await first

    asyncio.run(scenario())
    assert {a.name for a in load_config(force_reload=True).agents.named} == {"Merged", "C"}


def test_revival_write_failure_retains_snapshot(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.agents.medic_agent import MedicAgent
    from myclaw.config import load_config

    _agents(Coder={"system_prompt": "saved"}, Tester={})
    asyncio.run(evolve.fuse("Coder", "Tester", "Builder", "", "p", "Coder"))
    original = evolve._write_trash
    calls = []

    def fail_second(items):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk full")
        original(items)

    monkeypatch.setattr(evolve, "_write_trash", fail_second)
    with pytest.raises(evolve.LifecycleError, match="may already be active"):
        asyncio.run(MedicAgent().revive_agent("Coder"))
    assert any(a.name == "Coder" and a.system_prompt == "saved" for a in load_config(force_reload=True).agents.named)
    assert any(t.get("snapshot", {}).get("name") == "Coder" for t in json.loads(
        evolve._trash_file().read_text(encoding="utf-8")))
    assert all(t["agent"]["name"] != "Coder" for t in evolve.agent_trash())


def test_migration_combines_files_once_and_hides_sensitive_fields(home):  # noqa: F811
    from myclaw import evolve
    from myclaw.agents.medic_agent import MedicAgent

    _agents()
    legacy = {"agent": {"name": "Legacy", "model": "legacy-model", "system_prompt": "secret", "key": ""},
              "fused_into": "Builder", "at": "then"}
    current = {"agent": {"name": "Other", "system_prompt": "secret2"}, "snapshot": {"name": "Other",
               "system_prompt": "secret2"}, "fused_into": "Builder"}
    evolve._legacy_trash_file().write_text(json.dumps([legacy]), encoding="utf-8")
    evolve._trash_file().write_text(json.dumps([current]), encoding="utf-8")
    public = json.dumps(evolve.agent_trash())
    assert "secret" not in public and "snapshot" not in public
    assert {t["agent"]["name"] for t in evolve.agent_trash()} == {"Legacy", "Other"}
    revived = asyncio.run(MedicAgent().revive_agent("Legacy"))
    assert revived["agent"]["system_prompt"] == "secret" and revived["agent"]["model"] == "legacy-model"
    assert {t["agent"]["name"] for t in evolve.agent_trash()} == {"Other"}
    assert evolve._legacy_trash_file().exists()


def test_same_name_versions_are_selected_individually(home):  # noqa: F811
    from myclaw import evolve
    from myclaw.agents.medic_agent import MedicAgent
    from myclaw.config import load_config

    _agents()
    evolve._legacy_trash_file().write_text(json.dumps([
        {"agent": {"name": "A", "model": "older"}, "fused_into": "X"},
        {"agent": {"name": "A", "model": "newer"}, "fused_into": "Y"},
    ]), encoding="utf-8")
    first, second = evolve.agent_trash()
    assert first["id"] != second["id"]
    assert asyncio.run(MedicAgent().revive_agent("A", entry_id=second["id"]))["agent"]["model"] == "newer"
    _agents()
    assert [t["id"] for t in evolve.agent_trash()] == [first["id"]]
    asyncio.run(MedicAgent().revive_agent("A", entry_id=first["id"]))
    assert load_config(force_reload=True).agents.named[0].model == "older"


def test_production_reload_migrates_groups_and_both_dm_histories(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.chat import store as chat
    from myclaw.config import load_config
    from myclaw.web import api, chat_api, settings_api

    _agents(A={}, B={})
    group = chat.create_channel("team", ["A", "B"])
    for name in ("A", "B"):
        chat.create_channel(name, [name], topic=f"topic {name}", kind="dm", channel_id=f"dm-{name.lower()}")
        chat.add_message(f"dm-{name.lower()}", "user", "", f"history {name}", meta={"origin": name})

    class Engine:
        def agents(self):
            return ["default", *(x.name for x in load_config(force_reload=True).agents.named)]

    async def no_telegram():
        return None

    monkeypatch.setattr(chat_api, "engine", lambda: Engine())
    monkeypatch.setattr(api._telegram_api, "on_settings_changed", no_telegram)
    monkeypatch.setattr(api, "_agent_registry", {})
    monkeypatch.setattr(settings_api, "_on_config_changed", api._reset_agent_registry)
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    assert chat.get_channel(group["id"])["members"] == ["Merged"]
    messages = chat.all_messages("dm-merged")
    assert [(m["text"], m["meta"]["origin"]) for m in messages] == [("history A", "A"), ("history B", "B")]
    with chat._conn() as conn:
        origins = conn.execute("SELECT message_id, source_channel_id FROM fusion_message_origins").fetchall()
    assert {(row["message_id"], row["source_channel_id"]) for row in origins} == {
        (messages[0]["id"], "dm-a"), (messages[1]["id"], "dm-b")
    }
    assert chat.get_channel("dm-a")["topic"] == "topic A"
    assert chat.get_channel("dm-b")["topic"] == "topic B"


def test_retry_after_commit_marker_failure_does_not_duplicate_messages(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.chat import store as chat

    _agents(A={}, B={})
    for name in ("A", "B"):
        chat.create_channel(name, [name], kind="dm", channel_id=f"dm-{name.lower()}")
        chat.add_message(f"dm-{name.lower()}", "user", "", name)
    real = evolve._write_trash

    def fail_complete(items):
        if any(t.get("type") == "fusion" and t.get("state") == "complete" for t in items):
            raise OSError("marker write failed")
        real(items)

    monkeypatch.setattr(evolve, "_write_trash", fail_complete)
    with pytest.raises(evolve.LifecycleError, match="resume"):
        asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    operation = evolve.pending_fusions()[0]["id"]
    monkeypatch.setattr(evolve, "_write_trash", real)
    assert asyncio.run(evolve.resume_fusion(operation))["state"] == "complete"
    assert [m["text"] for m in chat.all_messages("dm-merged")] == ["A", "B"]


def test_open_websocket_cannot_run_removed_agent(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import evolve
    from myclaw.config import load_config
    from myclaw.web import api, settings_api

    _agents(A={}, B={})
    calls = []

    class Fake:
        async def stream_think(self, *args, **kwargs):
            calls.append(1)
            yield "unsafe"

    fake = Fake()
    monkeypatch.setattr(settings_api, "_on_config_changed", None)
    monkeypatch.setattr(api, "_ensure_registry", lambda: {
        a.name: fake for a in load_config(force_reload=True).agents.named
    })
    monkeypatch.setattr(api._mfa, "is_enabled_for_user", lambda _: False)
    monkeypatch.setattr(api, "check_quota", lambda *args: (True, 1))
    app = FastAPI()
    app.add_api_websocket_route("/ws/chat/{agent_name}", api.chat_websocket)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/chat/A") as ws:
            asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
            ws.send_text("hello")
            assert "no longer exists" in ws.receive_text()
    assert not calls


def test_stale_agent_object_cannot_run_after_fusion(home):  # noqa: F811
    from myclaw import evolve
    from myclaw.agent import Agent

    _agents(A={}, B={})
    stale = object.__new__(Agent)
    stale.name = "A"
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    assert "graveyard" in asyncio.run(stale.think("hello"))

    async def stream():
        return [s async for s in stale.stream_think("hello")]

    assert "graveyard" in asyncio.run(stream())[0]


def test_fusion_waits_for_running_agent_and_background_work(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.agent import Agent
    from myclaw.config import load_config

    _agents(A={}, B={})
    agent = object.__new__(Agent)
    agent.name = "A"
    agent._pending_preloads = set()
    agent._max_pending_preloads = 1

    async def scenario():
        entered, release, background_release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def running(*args):
            agent._track_preload(asyncio.create_task(background_release.wait()))
            entered.set()
            await release.wait()
            return "finished"

        monkeypatch.setattr(agent, "_tracked_think", running)
        task = asyncio.create_task(agent.think("work"))
        await entered.wait()
        with pytest.raises(evolve.LifecycleError, match="running turns"):
            await evolve.fuse("A", "B", "Merged", "", "p", "A")
        assert {a.name for a in load_config(force_reload=True).agents.named} == {"A", "B"}
        release.set()
        assert await task == "finished"
        with pytest.raises(evolve.LifecycleError, match="background work"):
            await evolve.fuse("A", "B", "Merged", "", "p", "A")
        background_release.set()
        await asyncio.gather(*agent._pending_preloads)
        # Background lease cleanup is scheduled asynchronously.
        for _ in range(50):
            try:
                await evolve.fuse("A", "B", "Merged", "", "p", "A")
                break
            except evolve.LifecycleError as exc:
                if "running turns" not in str(exc):
                    raise
                await asyncio.sleep(.02)
        else:
            pytest.fail("background execution lease was not released")

    asyncio.run(scenario())


def test_running_job_blocks_fusion_and_finishes_normally(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.jobs import store
    from myclaw.jobs.runner import JobRunner

    _agents(A={}, B={})
    job = store.create_job({"name": "work", "prompt": "p", "agent": "A"})
    runner = JobRunner(lambda: {})

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def work(job, run_id, agent=None, kind="run"):
            entered.set()
            await release.wait()
            await asyncio.to_thread(store.finish_run, run_id, "ok", "done")
            return "ok"

        monkeypatch.setattr(runner, "_execute_tracked", work)
        task = asyncio.create_task(runner.run_job(job["id"]))
        await entered.wait()
        with pytest.raises(evolve.LifecycleError, match="jobs"):
            await evolve.fuse("A", "B", "Merged", "", "p", "A")
        release.set()
        run_id = await task
        assert store.get_run(run_id)["output"] == "done"
        await evolve.fuse("A", "B", "Merged", "", "p", "A")

    asyncio.run(scenario())


def test_timed_out_worker_remains_tracked_until_thread_exits(home):  # noqa: F811
    import threading

    from myclaw import evolve
    from myclaw.executions import execution, tracked_to_thread

    _agents(A={}, B={})
    started, release = threading.Event(), threading.Event()

    def worker():
        started.set()
        release.wait()

    async def scenario():
        try:
            async with execution("A", "turn"):
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(tracked_to_thread(worker), .1)
                assert started.is_set()
            with pytest.raises(evolve.LifecycleError, match="running turns"):
                await evolve.fuse("A", "B", "Merged", "", "p", "A")
        finally:
            release.set()
        for _ in range(50):
            try:
                await evolve.fuse("A", "B", "Merged", "", "p", "A")
                break
            except evolve.LifecycleError as exc:
                if "running turns" not in str(exc):
                    raise
                await asyncio.sleep(.02)
        else:
            pytest.fail("worker lease was not released")

    asyncio.run(scenario())


def test_cross_process_execution_blocks_fusion_and_dead_process_is_reaped(home):  # noqa: F811
    import subprocess
    import sys

    from myclaw import evolve

    _agents(A={}, B={})
    code = ("import asyncio\nfrom myclaw.executions import execution\n"
            "async def run():\n"
            " async with execution('A', 'turn'):\n"
            "  print('ready', flush=True)\n"
            "  await asyncio.to_thread(input)\n"
            "asyncio.run(run())\n")
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(evolve.LifecycleError, match="running turns"):
            asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
        child.terminate()
        child.wait(timeout=10)
        asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()


def test_parent_agents_setting_cannot_revive_graveyard(home):  # noqa: F811
    from fastapi import HTTPException

    from myclaw import evolve
    from myclaw.config import load_config
    from myclaw.web import settings_api

    _agents(A={}, B={})
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    agents = load_config(force_reload=True).agents.model_dump(mode="json")
    agents["named"].append({"name": "A", "model": "unexpected"})
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(settings_api.update_settings(settings_api.SettingsUpdate(changes={"agents": agents})))
    assert conflict.value.status_code == 409
    assert [a.name for a in load_config(force_reload=True).agents.named] == ["Merged"]


def test_parent_agents_setting_cannot_bypass_pending_fusion(home, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from myclaw import evolve
    from myclaw.config import load_config
    from myclaw.web import settings_api

    _agents(A={}, B={})

    def fail(_):
        raise OSError("migration unavailable")

    monkeypatch.setattr(evolve, "_move_fusion_references", fail)
    with pytest.raises(evolve.LifecycleError):
        asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    agents = load_config(force_reload=True).agents.model_dump(mode="json")
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(settings_api.update_settings(settings_api.SettingsUpdate(changes={"agents": agents})))
    assert conflict.value.status_code == 409


def test_config_replace_failure_preserves_full_config(home, monkeypatch):  # noqa: F811
    from myclaw import config_encryption, evolve
    from myclaw.config import CONFIG_FILE, load_config

    _agents(A={"system_prompt": "saved A"}, B={}, C={"system_prompt": "saved C"})
    before = CONFIG_FILE.read_bytes()
    real_replace = config_encryption.os.replace

    def fail_config(src, dst):
        if str(dst) == str(CONFIG_FILE):
            raise OSError("injected replacement failure")
        return real_replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(config_encryption.os, "replace", fail_config)
        with pytest.raises(evolve.LifecycleError):
            asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    assert CONFIG_FILE.read_bytes() == before
    assert load_config(force_reload=True).agents.named[2].system_prompt == "saved C"
    operation = evolve.pending_fusions()[0]["id"]
    assert asyncio.run(evolve.resume_fusion(operation))["state"] == "aborted"


def test_config_write_failure_never_truncates_previous_settings(home, monkeypatch):  # noqa: F811
    from myclaw import config, config_encryption

    cfg = _agents(A={"system_prompt": "saved A"}, C={"system_prompt": "saved C"})
    before = config.CONFIG_FILE.read_bytes()
    cfg.agents.named[0].system_prompt = "new A"

    def fail(_):
        raise OSError("injected flush failure")

    with monkeypatch.context() as patch:
        patch.setattr(config_encryption.os, "fsync", fail)
        with pytest.raises(OSError, match="injected"):
            config.save_config(cfg)
    assert config.CONFIG_FILE.read_bytes() == before
    assert [a.system_prompt for a in config.load_config(force_reload=True).agents.named] == ["saved A", "saved C"]
    assert not list(config.CONFIG_FILE.parent.glob("config.json.*.tmp"))


def test_fusion_retired_dm_does_not_route_to_wrong_agent(home):  # noqa: F811
    from myclaw import evolve
    from myclaw.agents.medic_agent import MedicAgent
    from myclaw.chat import store
    from myclaw.chat.engine import ChatEngine

    _agents(A={}, B={})
    dm = store.dm_channel("A")
    store.add_message(dm["id"], "user", "", "original history")
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    engine = ChatEngine(lambda: {"default": object(), "Merged": object()})
    with pytest.raises(ValueError, match="retired"):
        asyncio.run(engine._open_channel(dm["id"]))
    with pytest.raises(ValueError, match="retired"):
        store.dm_channel("A")
    asyncio.run(MedicAgent().revive_agent("A"))
    restored = store.dm_channel("A")
    assert restored["members"] == ["A"] and restored["kind"] == "dm"
    assert [m["text"] for m in store.all_messages("dm-merged")] == ["original history"]


def test_backup_includes_graveyard_and_restores_in_fresh_profile(home, monkeypatch):  # noqa: F811
    import tarfile

    from myclaw import backup, config, evolve
    from myclaw.agents.medic_agent import MedicAgent
    from myclaw.chat import store as chat
    from myclaw.jobs import store as jobs
    from myclaw.web import settings_api

    monkeypatch.setattr(backup, "install_root", lambda: home)
    backup_dir = home / "archives"
    backup_dir.mkdir()
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(backup_dir))
    _agents(A={"system_prompt": "original"}, B={})
    chat.dm_channel("A")
    jobs.create_job({"name": "job", "prompt": "p", "agent": "A"})
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    archive = backup.create_backup(["settings", "memory", "jobs"])
    manifest = backup.read_manifest(backup_dir / archive["name"])
    assert "data/agents-graveyard.json" in manifest["files"]
    with tarfile.open(backup_dir / archive["name"], "r:gz") as tar:
        journal = json.load(tar.extractfile("data/agents-graveyard.json"))
    assert any(item.get("type") == "fusion" and item.get("state") == "complete" for item in journal)
    fresh = home / "fresh"
    fresh.mkdir()
    monkeypatch.setenv("HOME", str(fresh))
    monkeypatch.setenv("USERPROFILE", str(fresh))
    monkeypatch.setattr(config, "CONFIG_FILE", fresh / ".myclaw" / "config.json")
    monkeypatch.setattr(settings_api, "CONFIG_FILE", config.CONFIG_FILE)
    config._cached_config = None
    backup.restore_backup(archive["name"])
    assert {t["agent"]["name"] for t in evolve.agent_trash()} == {"A", "B"}
    assert asyncio.run(MedicAgent().revive_agent("A"))["agent"]["system_prompt"] == "original"
    assert chat.dm_channel("A")["members"] == ["A"]


def test_chat_write_during_restore_is_not_lost(home, monkeypatch):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
    from pathlib import Path

    from myclaw import backup
    from myclaw.chat import store as chat

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    chat.ensure_default_channel()
    chat.add_message("general", "user", "", "in archive")
    archive = backup.create_backup(["settings", "memory"])
    chat.add_message("general", "user", "", "before restore")
    restoring = threading.Event()
    proceed = threading.Event()
    real_copy = backup.shutil.copy2

    def pause_copy(src, dst, *args, **kwargs):
        if Path(dst).name == "chat.db.restoring":
            restoring.set()
            if not proceed.wait(10):
                raise TimeoutError("Restore test timed out")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(backup.shutil, "copy2", pause_copy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        restore = pool.submit(backup.restore_backup, archive["name"])
        try:
            assert restoring.wait(10)
            writer = pool.submit(chat.add_message, "general", "user", "", "during restore")
            try:
                writer.result(timeout=1)
            except FutureTimeout:
                pass
        finally:
            proceed.set()
        restore.result(timeout=10)
        writer.result(timeout=10)
    assert [m["text"] for m in chat.list_messages("general")] == ["in archive", "during restore"]


def test_chat_write_from_another_process_waits_for_restore(home, monkeypatch):  # noqa: F811
    import subprocess
    import sys
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from myclaw import backup
    from myclaw.chat import store as chat

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    chat.ensure_default_channel()
    chat.add_message("general", "user", "", "in archive")
    archive = backup.create_backup(["settings", "memory"])
    restoring = threading.Event()
    proceed = threading.Event()
    real_copy = backup.shutil.copy2

    def pause_copy(src, dst, *args, **kwargs):
        if Path(dst).name == "chat.db.restoring":
            restoring.set()
            if not proceed.wait(25):
                raise TimeoutError("Restore test timed out")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(backup.shutil, "copy2", pause_copy)
    script = ("from myclaw.chat import store; print('started', flush=True); "
              "store.add_message('general', 'user', '', 'other process')")
    with ThreadPoolExecutor(max_workers=1) as pool:
        restore = pool.submit(backup.restore_backup, archive["name"])
        process = None
        try:
            assert restoring.wait(10)
            process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            assert process.stdout.readline().strip() == "started"
            try:
                status = process.wait(timeout=11)
            except subprocess.TimeoutExpired:
                status = None
            assert status is None, process.stderr.read() if status is not None else ""
        finally:
            proceed.set()
        restore.result(timeout=10)
        _, error = process.communicate(timeout=10)
        assert process.returncode == 0, error
    assert [m["text"] for m in chat.list_messages("general")] == ["in archive", "other process"]


def test_chat_database_guard_allows_nested_chat_access(home):  # noqa: F811
    from myclaw.chat import store as chat

    with chat.database_guard():
        chat.ensure_default_channel()
        chat.add_message("general", "user", "", "nested")
    assert chat.list_messages("general")[0]["text"] == "nested"


def test_failed_restore_rejects_waiting_chat_write(home, monkeypatch):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from myclaw import backup
    from myclaw.chat import store as chat

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    chat.ensure_default_channel()
    chat.add_message("general", "user", "", "in archive")
    archive = backup.create_backup(["settings", "memory"])
    chat.add_message("general", "user", "", "before restore")
    restoring = threading.Event()
    proceed = threading.Event()
    real_copy = backup.shutil.copy2

    def fail_copy(src, dst, *args, **kwargs):
        if Path(dst).name == "chat.db.restoring":
            restoring.set()
            if not proceed.wait(10):
                raise TimeoutError("Restore test timed out")
            raise OSError("injected restore failure")
        return real_copy(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(backup.shutil, "copy2", fail_copy)
        with ThreadPoolExecutor(max_workers=2) as pool:
            restore = pool.submit(backup.restore_backup, archive["name"])
            try:
                assert restoring.wait(10)
                writer = pool.submit(chat.add_message, "general", "user", "", "during restore")
            finally:
                proceed.set()
            with pytest.raises(ValueError, match="Restore incomplete"):
                restore.result(timeout=10)
            with pytest.raises(ValueError, match="restore.*recovery"):
                writer.result(timeout=10)
    marker = home / ".myclaw" / ".lifecycle-restore-pending.json"
    safety = json.loads(marker.read_text(encoding="utf-8"))["safety_backup"]
    backup.restore_backup(safety)
    assert [m["text"] for m in chat.list_messages("general")] == ["in archive", "before restore"]


def test_restore_rejects_waiting_write_to_missing_channel(home, monkeypatch):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from myclaw import backup
    from myclaw.chat import store as chat

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    chat.ensure_default_channel()
    archive = backup.create_backup(["settings", "memory"])
    chat.create_channel("new", ["default"], channel_id="new")
    restoring = threading.Event()
    proceed = threading.Event()
    real_copy = backup.shutil.copy2

    def pause_copy(src, dst, *args, **kwargs):
        if Path(dst).name == "chat.db.restoring":
            restoring.set()
            if not proceed.wait(10):
                raise TimeoutError("Restore test timed out")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(backup.shutil, "copy2", pause_copy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        restore = pool.submit(backup.restore_backup, archive["name"])
        try:
            assert restoring.wait(10)
            writer = pool.submit(chat.add_message, "new", "user", "", "during restore")
        finally:
            proceed.set()
        restore.result(timeout=10)
        with pytest.raises(ValueError, match="channel.*no longer exists"):
            writer.result(timeout=10)
    assert chat.list_messages("new") == []


def test_restore_refuses_directory_link_outside_data(home, monkeypatch):  # noqa: F811
    import os

    from myclaw import backup

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    link = home / ".myclaw" / "knowledge" / "default" / "linked"
    link.mkdir(parents=True)
    (link / "note.md").write_text("archive", encoding="utf-8")
    archive = backup.create_backup(["settings", "knowledge"])
    link.rename(link.with_name("saved"))
    outside = home / "outside"
    outside.mkdir()
    note = outside / "note.md"
    note.write_text("outside", encoding="utf-8")
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="link"):
        backup.restore_backup(archive["name"])
    assert note.read_text(encoding="utf-8") == "outside"


def test_backup_refuses_symlinked_knowledge_root(home, monkeypatch):  # noqa: F811
    import os

    from myclaw import backup

    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    root = home / ".myclaw"
    knowledge = root / "knowledge"
    outside = home / "knowledge"
    knowledge.mkdir()
    outside.mkdir()
    knowledge.rename(root / "knowledge-saved")
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(knowledge))
    else:
        knowledge.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="directory link"):
        backup.create_backup(["knowledge"])


def test_post_rejects_reused_dm_after_restore(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import backup
    from myclaw.chat import store as chat
    from myclaw.web import chat_api
    from tests.test_chat_message_actions import FakeAgent

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    chat.dm_channel("Foo")
    archive = backup.create_backup(["settings", "memory"])
    chat.delete_channel("dm-foo")
    chat.dm_channel("FOO")
    chat_api.set_registry_getter(lambda: {"default": FakeAgent("default"), "Foo": FakeAgent("Foo"),
                                          "FOO": FakeAgent("FOO")})
    engine = chat_api.engine()
    original = engine._open_channel

    async def restore_after_validation(channel_id):
        channel = await original(channel_id)
        await asyncio.to_thread(backup.restore_backup, archive["name"])
        return channel

    monkeypatch.setattr(engine, "_open_channel", restore_after_validation)
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as client:
        response = client.post("/api/channels/dm-foo/messages", json={"text": "for FOO"})
    assert response.status_code == 400
    assert not chat.list_messages("dm-foo")


def test_edit_rejects_reused_message_after_restore(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import backup
    from myclaw.chat import store as chat
    from myclaw.web import chat_api
    from tests.test_chat_message_actions import FakeAgent

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    chat.ensure_default_channel()
    empty = backup.create_backup(["settings", "memory"])
    original = chat.add_message("general", "user", "", "A")
    archive = backup.create_backup(["settings", "memory"])
    backup.restore_backup(empty["name"])
    replacement = chat.add_message("general", "user", "", "B")
    assert original["id"] == replacement["id"]
    chat_api.set_registry_getter(lambda: {"default": FakeAgent("default")})
    edit = chat.edit_message

    def restore_before_edit(*args, **kwargs):
        backup.restore_backup(archive["name"])
        return edit(*args, **kwargs)

    monkeypatch.setattr(chat, "edit_message", restore_before_edit)
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as client:
        response = client.patch(f"/api/channels/general/messages/{replacement['id']}",
                                json={"text": "edit B"})
    assert response.status_code == 400
    assert chat.get_message(original["id"])["text"] == "A"


def test_delete_rejects_reused_message_after_restore(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import backup
    from myclaw.chat import store as chat
    from myclaw.web import chat_api
    from tests.test_chat_message_actions import FakeAgent

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    chat.ensure_default_channel()
    empty = backup.create_backup(["settings", "memory"])
    original = chat.add_message("general", "user", "", "A")
    archive = backup.create_backup(["settings", "memory"])
    backup.restore_backup(empty["name"])
    replacement = chat.add_message("general", "user", "", "B")
    assert original["id"] == replacement["id"]
    chat_api.set_registry_getter(lambda: {"default": FakeAgent("default")})
    delete = chat.delete_messages

    def restore_before_delete(*args, **kwargs):
        backup.restore_backup(archive["name"])
        return delete(*args, **kwargs)

    monkeypatch.setattr(chat, "delete_messages", restore_before_delete)
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as client:
        response = client.delete(f"/api/channels/general/messages/{replacement['id']}")
    assert response.status_code == 400
    assert chat.get_message(original["id"])["text"] == "A"


def test_clear_rejects_reused_messages_after_restore(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import backup
    from myclaw.chat import store as chat
    from myclaw.web import chat_api

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    chat.ensure_default_channel()
    empty = backup.create_backup(["settings", "memory"])
    original = chat.add_message("general", "user", "", "A")
    archive = backup.create_backup(["settings", "memory"])
    backup.restore_backup(empty["name"])
    replacement = chat.add_message("general", "user", "", "B")
    assert original["id"] == replacement["id"]
    clear = chat.clear_messages

    def restore_before_clear(*args, **kwargs):
        backup.restore_backup(archive["name"])
        return clear(*args, **kwargs)

    monkeypatch.setattr(chat, "clear_messages", restore_before_clear)
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as client:
        response = client.delete("/api/channels/general/messages")
    assert response.status_code == 400
    assert chat.get_message(original["id"])["text"] == "A"


def test_delete_with_replies_rejects_restore_between_reads(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import backup
    from myclaw.chat import store as chat
    from myclaw.web import chat_api
    from tests.test_chat_message_actions import FakeAgent

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    chat.ensure_default_channel()
    question = chat.add_message("general", "user", "", "question")
    answer = chat.add_message("general", "agent", "default", "old reply")
    archive = backup.create_backup(["settings", "memory"])
    chat.edit_message(answer["id"], "new reply")
    chat_api.set_registry_getter(lambda: {"default": FakeAgent("default")})
    after = chat.messages_after
    calls = 0

    def restore_between_reads(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            backup.restore_backup(archive["name"])
        return after(*args, **kwargs)

    monkeypatch.setattr(chat, "messages_after", restore_between_reads)
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as client:
        response = client.delete(f"/api/channels/general/messages/{question['id']}?with_replies=true")
    assert response.status_code == 400
    assert [m["text"] for m in chat.list_messages("general")] == ["question", "old reply"]


def test_delete_started_during_restore_rejects_restored_replies(home, monkeypatch):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import backup
    from myclaw.chat import store as chat
    from myclaw.web import chat_api
    from tests.test_chat_message_actions import FakeAgent

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(home / "archives"))
    chat.ensure_default_channel()
    question = chat.add_message("general", "user", "", "question")
    chat.add_message("general", "agent", "default", "old reply")
    archive = backup.create_backup(["settings", "memory"])
    chat_api.set_registry_getter(lambda: {"default": FakeAgent("default")})
    restoring = threading.Event()
    proceed = threading.Event()
    read_generation = threading.Event()
    real_copy = backup.shutil.copy2
    real_generation = chat.restore_generation

    def pause_copy(src, dst, *args, **kwargs):
        if Path(dst).name == "chat.db.restoring":
            restoring.set()
            if not proceed.wait(10):
                raise TimeoutError("Restore test timed out")
        return real_copy(src, dst, *args, **kwargs)

    def observe_generation():
        generation = real_generation()
        read_generation.set()
        return generation

    monkeypatch.setattr(backup.shutil, "copy2", pause_copy)
    monkeypatch.setattr(chat, "restore_generation", observe_generation)
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        restore = pool.submit(backup.restore_backup, archive["name"])
        try:
            assert restoring.wait(10)
            deleting = pool.submit(client.delete,
                                   f"/api/channels/general/messages/{question['id']}?with_replies=true")
            assert read_generation.wait(10)
        finally:
            proceed.set()
        restore.result(timeout=10)
        assert deleting.result(timeout=10).status_code == 400
    assert [m["text"] for m in chat.list_messages("general")] == ["question", "old reply"]


def test_chat_read_reports_restore_recovery(home):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.web import chat_api

    marker = home / ".myclaw" / ".lifecycle-restore-pending.json"
    marker.write_text('{"safety_backup": "before-restore.tar.gz"}', encoding="utf-8")
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as client:
        for path in ("/api/channels", "/api/channels/general/messages"):
            response = client.get(path)
            assert response.status_code == 503
            assert "restore needs recovery" in response.json()["detail"]
        with client.websocket_connect("/ws/channels/general") as socket:
            response = socket.receive_json()
            assert response["type"] == "error"
            assert "restore needs recovery" in response["error"]


def test_backup_refuses_pending_fusion_until_journal_recovers(home, monkeypatch):  # noqa: F811
    from myclaw import backup, evolve

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents(A={}, B={})
    def fail(_):
        raise OSError("injected")

    with monkeypatch.context() as patch:
        patch.setattr(evolve, "_move_fusion_references", fail)
        with pytest.raises(evolve.LifecycleError):
            asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    operation = evolve.pending_fusions()[0]["id"]
    with pytest.raises(ValueError, match="recover pending fusions"):
        backup.create_backup(["settings", "memory", "jobs"])
    assert asyncio.run(evolve.resume_fusion(operation))["state"] == "complete"
    archive = backup.create_backup(["settings", "memory", "jobs"])
    assert "data/agents-graveyard.json" in backup.read_manifest(backup.backup_dir() / archive["name"])["files"]


def test_backup_includes_legacy_graveyard_and_refuses_partial_restore(home, monkeypatch):  # noqa: F811
    from myclaw import backup, evolve

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents()
    evolve._legacy_trash_file().write_text(
        json.dumps([{"agent": {"name": "Legacy", "model": "m"}}]), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="requires memory and jobs"):
        backup.create_backup(["settings"])
    archive = backup.create_backup(["settings", "memory", "jobs"])
    manifest = backup.read_manifest(backup.backup_dir() / archive["name"])
    assert "data/agents-trash.json" in manifest["files"]
    with pytest.raises(ValueError, match="memory and jobs"):
        backup.restore_backup(archive["name"], parts=["settings"])


def test_legacy_restore_replaces_newer_graveyard_as_a_set(home, monkeypatch):  # noqa: F811
    from myclaw import backup, evolve
    from myclaw.chat import store as chat
    from myclaw.jobs import store as jobs

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents(A={}, B={})
    chat.dm_channel("A")
    jobs.create_job({"name": "a", "prompt": "p", "agent": "A"})
    legacy = evolve._legacy_trash_file()
    legacy.write_text(json.dumps([{"agent": {"name": "Old"}}]), encoding="utf-8")
    archive = backup.create_backup(["settings", "memory", "jobs"])
    legacy.write_text(json.dumps([{"agent": {"name": "New"}}]), encoding="utf-8")
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    backup.restore_backup(archive["name"])
    assert {item["agent"]["name"] for item in evolve.agent_trash()} == {"Old"}
    assert not evolve._trash_file().exists()
    saved = list((home / ".myclaw" / ".trash").glob("restore-*/data/agents-graveyard.json"))
    assert len(saved) == 1 and "Merged" in saved[0].read_text(encoding="utf-8")


def test_failed_legacy_restore_recovers_newer_graveyard(home, monkeypatch):  # noqa: F811
    from myclaw import backup, config, evolve
    from myclaw.chat import store as chat
    from myclaw.jobs import store as jobs

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents(A={}, B={})
    chat.dm_channel("A")
    jobs.create_job({"name": "a", "prompt": "p", "agent": "A"})
    legacy = evolve._legacy_trash_file()
    legacy.write_text(json.dumps([{"agent": {"name": "Old"}}]), encoding="utf-8")
    archive = backup.create_backup(["settings", "memory", "jobs"])
    legacy.write_text(json.dumps([{"agent": {"name": "New"}}]), encoding="utf-8")
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    before = evolve._trash_file().read_bytes()
    real_load = config.load_config

    def fail_reload(*args, **kwargs):
        if kwargs.get("force_reload"):
            raise RuntimeError("injected reload failure")
        return real_load(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(config, "load_config", fail_reload)
        with pytest.raises(ValueError, match="safety backup"):
            backup.restore_backup(archive["name"])
    marker = home / ".myclaw" / ".lifecycle-restore-pending.json"
    safety = json.loads(marker.read_text(encoding="utf-8"))["safety_backup"]
    backup.restore_backup(safety)
    assert evolve._trash_file().read_bytes() == before
    assert {t["agent"]["name"] for t in evolve.agent_trash()} == {"A", "B", "New"}


def test_unreadable_encryption_key_fails_backup_and_safety_backup(home, monkeypatch):  # noqa: F811
    from myclaw import backup, config, config_encryption, evolve

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setattr(config_encryption, "KEY_FILE", home / ".myclaw" / ".config_key")
    monkeypatch.setattr(config_encryption, "_get_keyring_password", lambda: None)
    monkeypatch.setattr(config_encryption, "_set_keyring_password", lambda _: False)
    monkeypatch.delenv("ZENSYNORA_CONFIG_KEY", raising=False)
    _agents(A={})
    archive = backup.create_backup(["settings", "memory", "jobs"])
    config_encryption.encrypt_config(config.CONFIG_FILE)
    encrypted = config.CONFIG_FILE.read_bytes()
    real_copy = backup.shutil.copy2

    def fail_key(src, dst, *args, **kwargs):
        if str(src).endswith(".config_key"):
            raise OSError("injected key read failure")
        return real_copy(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(backup.shutil, "copy2", fail_key)
        with pytest.raises(evolve.LifecycleError, match=r"Cannot back up \.config_key"):
            backup.create_backup(["settings", "memory", "jobs"])
        with pytest.raises(evolve.LifecycleError, match=r"Cannot back up \.config_key"):
            backup.restore_backup(archive["name"])
    assert config.CONFIG_FILE.read_bytes() == encrypted
    assert config.load_config(force_reload=True).agents.named[0].name == "A"
    assert not (home / ".myclaw" / ".lifecycle-restore-pending.json").exists()
    assert len(backup.list_backups()) == 1


def test_lifecycle_backup_rejects_incomplete_default_parts(home, monkeypatch):  # noqa: F811
    from myclaw import backup, evolve
    from myclaw.config import load_config

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents(A={}, B={})
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    cfg = load_config(force_reload=True)
    cfg.backup.include_memory = False
    with pytest.raises(ValueError, match="requires memory and jobs"):
        backup.create_backup(cfg=cfg)
    cfg.backup.include_memory = True
    cfg.backup.include_jobs = False
    with pytest.raises(ValueError, match="requires memory and jobs"):
        backup.create_backup(cfg=cfg)
    assert not backup.list_backups()


def test_safety_backup_fails_closed_when_note_cannot_be_copied(home, monkeypatch):  # noqa: F811
    from myclaw import backup, evolve

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents(A={})
    note = home / ".myclaw" / "knowledge" / "default" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("before", encoding="utf-8")
    archive = backup.create_backup(["settings", "knowledge", "memory", "jobs"])
    note.write_text("after", encoding="utf-8")
    real_copy = backup.shutil.copy2

    def fail_note(src, dst, *args, **kwargs):
        if src == note:
            raise OSError("injected note read failure")
        return real_copy(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(backup.shutil, "copy2", fail_note)
        with pytest.raises(evolve.LifecycleError, match="Cannot back up.*note.md"):
            backup.restore_backup(archive["name"])
    assert note.read_text(encoding="utf-8") == "after"
    assert not (home / ".myclaw" / ".lifecycle-restore-pending.json").exists()


def test_failed_restore_recovery_trashes_introduced_note(home, monkeypatch):  # noqa: F811
    from myclaw import backup, config

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents(A={})
    base = home / ".myclaw" / "knowledge" / "default"
    note = base / "folder" / "deep" / "imported.md"
    note.parent.mkdir(parents=True)
    note.write_text("archive note", encoding="utf-8")
    archive = backup.create_backup(["settings", "knowledge", "memory", "jobs"])
    note.unlink()
    note.parent.rmdir()
    note.parent.parent.rmdir()
    real_load = config.load_config

    def fail_reload(*args, **kwargs):
        if kwargs.get("force_reload"):
            raise RuntimeError("injected reload failure")
        return real_load(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(config, "load_config", fail_reload)
        with pytest.raises(ValueError, match="safety backup"):
            backup.restore_backup(archive["name"])
    assert note.read_text(encoding="utf-8") == "archive note"
    marker = home / ".myclaw" / ".lifecycle-restore-pending.json"
    safety = json.loads(marker.read_text(encoding="utf-8"))["safety_backup"]
    backup.restore_backup(safety)
    assert not note.exists() and not marker.exists()
    assert not (base / "folder").exists()
    saved = list((base / ".trash").glob("restore-*/folder/deep/imported.md"))
    assert len(saved) == 1 and saved[0].read_text(encoding="utf-8") == "archive note"


def test_failed_restore_preserves_introduced_note_edited_during_recovery(home, monkeypatch):  # noqa: F811
    from myclaw import backup, config

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents(A={})
    note = home / ".myclaw" / "knowledge" / "default" / "imported.md"
    note.parent.mkdir(parents=True)
    note.write_text("archive note", encoding="utf-8")
    archive = backup.create_backup(["settings", "knowledge", "memory", "jobs"])
    note.unlink()
    real_load = config.load_config

    def fail_reload(*args, **kwargs):
        if kwargs.get("force_reload"):
            raise RuntimeError("injected reload failure")
        return real_load(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(config, "load_config", fail_reload)
        with pytest.raises(ValueError, match="safety backup"):
            backup.restore_backup(archive["name"])
    note.write_text("new user edit", encoding="utf-8")
    marker = home / ".myclaw" / ".lifecycle-restore-pending.json"
    safety = json.loads(marker.read_text(encoding="utf-8"))["safety_backup"]
    with pytest.raises(ValueError, match="changed since restore"):
        backup.restore_backup(safety)
    assert note.read_text(encoding="utf-8") == "new user edit" and marker.exists()


def test_backup_restore_keeps_previous_note_version_in_trash(home, monkeypatch):  # noqa: F811
    from myclaw import backup

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents(A={})
    note = home / ".myclaw" / "knowledge" / "default" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("archive note", encoding="utf-8")
    archive = backup.create_backup(["settings", "knowledge", "memory", "jobs"])
    note.write_text("current note", encoding="utf-8")
    backup.restore_backup(archive["name"])
    assert note.read_text(encoding="utf-8") == "archive note"
    saved = list((note.parent / ".trash").glob("restore-*/note.md"))
    assert any(p.read_text(encoding="utf-8") == "current note" for p in saved)


@pytest.mark.parametrize("part", ["settings", "memory", "jobs"])
def test_backup_refuses_any_partial_lifecycle_restore(home, monkeypatch, part):  # noqa: F811
    from myclaw import backup, evolve
    from myclaw.chat import store as chat
    from myclaw.jobs import store as jobs

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents(A={}, B={})
    chat.dm_channel("A")
    job = jobs.create_job({"name": "a", "prompt": "p", "agent": "A"})
    old_archive = backup.create_backup(["settings", "memory", "jobs"])
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    archive = backup.create_backup(["settings", "memory", "jobs"])
    with pytest.raises(ValueError, match="no graveyard"):
        backup.restore_backup(old_archive["name"], parts=[part])
    with pytest.raises(ValueError, match="no graveyard|memory and jobs"):
        backup.restore_backup(archive["name"], parts=[part])
    assert jobs.get_job(job["id"])["agent"] == "Merged"
    assert chat.dm_channel("Merged")["members"] == ["Merged"]
    assert {t["agent"]["name"] for t in evolve.agent_trash()} == {"A", "B"}


def test_failed_backup_restore_blocks_agents_until_safety_recovery(home, monkeypatch):  # noqa: F811
    from myclaw import backup, evolve
    from myclaw.config import CONFIG_FILE, load_config

    monkeypatch.setattr(backup, "install_root", lambda: home)
    _agents(A={}, B={})
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    archive = backup.create_backup(["settings", "memory", "jobs"])
    before = CONFIG_FILE.read_bytes()
    real_copy = backup.shutil.copy2

    def fail_config(src, dst, *args, **kwargs):
        if str(dst).endswith("config.json.restoring"):
            raise OSError("injected restore failure")
        return real_copy(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(backup.shutil, "copy2", fail_config)
        with pytest.raises(ValueError, match="safety backup"):
            backup.restore_backup(archive["name"])
    marker = home / ".myclaw" / ".lifecycle-restore-pending.json"
    safety = json.loads(marker.read_text(encoding="utf-8"))["safety_backup"]
    with pytest.raises(evolve.LifecycleError, match="backup restore"):
        asyncio.run(evolve.set_level("Merged", 2))
    backup.restore_backup(safety)
    assert not marker.exists() and CONFIG_FILE.read_bytes() == before
    assert [a.name for a in load_config(force_reload=True).agents.named] == ["Merged"]


def test_restore_recovery_preserves_newly_introduced_graveyard(home, monkeypatch):  # noqa: F811
    from myclaw import backup, config, evolve
    from myclaw.web import settings_api

    monkeypatch.setattr(backup, "install_root", lambda: home)
    archives = home / "archives"
    archives.mkdir()
    monkeypatch.setenv("MYCLAW_BACKUP_DIR", str(archives))
    _agents(A={}, B={})
    asyncio.run(evolve.fuse("A", "B", "Merged", "", "p", "A"))
    archive = backup.create_backup(["settings", "memory", "jobs"])
    fresh = home / "other"
    fresh.mkdir()
    (fresh / ".myclaw").mkdir()
    monkeypatch.setenv("HOME", str(fresh))
    monkeypatch.setenv("USERPROFILE", str(fresh))
    monkeypatch.setattr(config, "CONFIG_FILE", fresh / ".myclaw" / "config.json")
    monkeypatch.setattr(settings_api, "CONFIG_FILE", config.CONFIG_FILE)
    config._cached_config = None
    _agents(Existing={})
    real_copy = backup.shutil.copy2

    def fail_config(src, dst, *args, **kwargs):
        if str(dst).endswith("config.json.restoring"):
            raise OSError("injected restore failure")
        return real_copy(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(backup.shutil, "copy2", fail_config)
        with pytest.raises(ValueError, match="safety backup"):
            backup.restore_backup(archive["name"])
    marker = fresh / ".myclaw" / ".lifecycle-restore-pending.json"
    safety = json.loads(marker.read_text(encoding="utf-8"))["safety_backup"]
    backup.restore_backup(safety)
    assert not evolve._trash_file().exists()
    copies = list((fresh / ".myclaw" / ".trash").glob("restore-*/data/agents-graveyard.json"))
    assert len(copies) == 1 and "Merged" in copies[0].read_text(encoding="utf-8")
    assert [a.name for a in config.load_config(force_reload=True).agents.named] == ["Existing"]


def test_parallel_tool_timeout_keeps_execution_lease(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.executions import execution
    from myclaw.tools.core import ParallelToolExecutor

    _agents(A={}, B={})

    async def stalled(*args):
        await asyncio.sleep(10)

    async def run():
        executor = ParallelToolExecutor(timeout=0.01)
        monkeypatch.setattr(executor, "_execute_single_tool", stalled)
        async with execution("A", "turn"):
            result = await executor.execute_tools([{"function": {"name": "slow"}}])
            assert "timed out" in result[0]["error"]
        with pytest.raises(evolve.LifecycleError, match="restarting its process"):
            await evolve.fuse("A", "B", "Merged", "", "p", "A")

    asyncio.run(run())


def test_corrupt_graveyard_and_concurrent_operations_fail_closed(home):  # noqa: F811
    from myclaw import evolve
    from myclaw.config import load_config

    _agents(A={}, B={})
    evolve._trash_file().write_text("{broken", encoding="utf-8")
    with pytest.raises(evolve.LifecycleError, match="repair"):
        asyncio.run(evolve.fuse("A", "B", "C", "", "p", "A"))
    assert [a.name for a in load_config(force_reload=True).agents.named] == ["A", "B"]
    evolve._trash_file().unlink()
    with evolve._lifecycle_guard():
        with pytest.raises(evolve.LifecycleError, match="running"):
            asyncio.run(evolve.fuse("A", "B", "C", "", "p", "A"))


def test_disabled_legacy_entry_without_snapshot_is_not_revived(home):  # noqa: F811
    from myclaw import evolve
    from myclaw.agents.medic_agent import MedicAgent
    from myclaw.config import load_config

    _agents()
    evolve._legacy_trash_file().write_text(json.dumps([
        {"agent": {"name": "Lost", "model": ""}, "llm_disabled": True}
    ]), encoding="utf-8")
    with pytest.raises(ValueError, match="no complete snapshot"):
        asyncio.run(MedicAgent().revive_agent("Lost"))
    assert not load_config(force_reload=True).agents.named


def test_guarded_api_calls_medic_and_redacts_graveyard(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import evolve
    from myclaw.agents.medic_agent import MedicAgent
    from myclaw.web import evolve_api

    _agents(Coder={"system_prompt": "TOP_SECRET"}, Tester={})
    asyncio.run(evolve.fuse("Coder", "Tester", "Builder", "", "p", "Coder"))
    called = []
    real = MedicAgent.revive_agent

    async def tracked(self, name, mode="active", entry_id=None):
        called.append((name, mode))
        return await real(self, name, mode, entry_id)

    monkeypatch.setattr(MedicAgent, "revive_agent", tracked)
    app = FastAPI()
    app.include_router(evolve_api.router)
    plain = TestClient(app, headers={})
    assert plain.post("/api/evolve/graveyard/revive", json={"name": "Coder"}).status_code == 403
    assert not called
    client = TestClient(app)
    overview = client.get("/api/evolve/overview")
    assert overview.status_code == 200 and "TOP_SECRET" not in overview.text and "snapshot" not in overview.text
    result = client.post("/api/evolve/graveyard/revive", json={"name": "Coder", "mode": "fusion"})
    assert result.status_code == 200 and called == [("Coder", "fusion")]


def test_change_request_becomes_a_job(home, monkeypatch):  # noqa: F811
    from myclaw import evolve
    from myclaw.jobs import store as jobs

    started = []
    monkeypatch.setattr(evolve, "_start_job", lambda job_id: started.append(job_id))
    r = evolve.request_change(action="optimise", target_kind="function", target="myclaw/knowledge/overview.py:_fill",
                              goal="Make it faster on 5000 notes.", agent="default")
    job = jobs.get_job(r["job_id"])
    assert started == [r["job_id"]] and job["name"].startswith("Evolve: ") and job["schedule"] == "manual"
    p = job["prompt"]
    for must in ("myclaw/knowledge/overview.py:_fill", "Make it faster on 5000 notes.", "read_source_file",
                 "propose_code_change", "approve", "smallest"):
        assert must in p
    assert job["max_steps"] >= 20
    with pytest.raises(ValueError):
        evolve.request_change(action="optimise", target_kind="function", target="", goal="  ", agent="default")
    llm = evolve.request_change(action="optimise", target_kind="llm", target="", goal="Cheaper models.", agent="default")
    assert "Do not change code" in jobs.get_job(llm["job_id"])["prompt"]
    assert [j["id"] for j in evolve.change_requests()] == [llm["job_id"], r["job_id"]]

    def boom(job_id):
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(evolve, "_start_job", boom)
    with pytest.raises(RuntimeError):
        evolve.request_change(action="edit", target_kind="code", target="", goal="x", agent="default")
    assert len(evolve.change_requests()) == 2          # the job that could not start is gone


def test_api_routes(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw import evolve
    from myclaw.web import evolve_api

    _agents(Coder={"model": "m1"})
    started = []

    class Runner:  # the real _start_job runs; starting a job needs the server's event loop
        def run_in_background(self, job_id, trigger="manual"):
            asyncio.get_running_loop()
            started.append(job_id)

    monkeypatch.setattr("myclaw.web.jobs_api.runner", lambda: Runner())
    app = FastAPI()
    app.include_router(evolve_api.router)
    c = TestClient(app)
    o = c.get("/api/evolve/overview").json()
    assert {"agents", "proposals", "requests", "trash", "self_update", "models"} <= set(o)
    assert [a["name"] for a in o["agents"]] == ["default", "Coder"] and o["agents"][1]["level"] == 1
    assert c.put("/api/evolve/agents/Coder/level", json={"level": 7}).json()["level"] == 7
    assert c.put("/api/evolve/agents/Coder/level", json={"level": 12}).status_code == 422
    r = c.post("/api/evolve/request", json={"action": "add", "target_kind": "script", "target": "scripts/x.sh",
                                            "goal": "Back up the notes.", "agent": "default"})
    assert r.status_code == 200 and started == [r.json()["job_id"]]
    assert c.post("/api/evolve/fuse", json={"a": "Coder", "b": "Coder", "name": "X", "description": "",
                                            "system_prompt": "p", "model_from": "Coder"}).status_code == 400
    async def evaluate(*, limit):
        return {"candidates": [{"a": "Coder", "b": "default"}], "assessed_pairs": 1, "checked_pairs": limit}

    monkeypatch.setattr("myclaw.fusion_evaluation.evaluate_fusion_candidates", evaluate)
    ev = c.post("/api/evolve/fuse/evaluate", json={"limit": 5})
    assert ev.status_code == 200 and ev.json()["checked_pairs"] == 5
    assert "blocked_reason" in ev.json()["candidates"][0]

    async def unavailable(*, limit):
        raise ValueError("Select a local model.")

    monkeypatch.setattr("myclaw.fusion_evaluation.evaluate_fusion_candidates", unavailable)
    assert c.post("/api/evolve/fuse/evaluate", json={"limit": 5}).status_code == 400
    assert c.post("/api/evolve/trash/restore", json={"name": "Coder"}).status_code == 403
