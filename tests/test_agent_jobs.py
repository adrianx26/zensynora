"""Agents and the Jobs page (2026-10-21): create, list, run, check, pause jobs;
the runner executes them with the job's agent and records/reports each run."""
import asyncio
import time

import pytest


class _Agent:
    def __init__(self, name, answer="Done: 3 items checked."):
        self.name, self.model, self._provider_name, self.answer = name, "m", "p", answer
        self.prompts = []

    async def think(self, prompt, user_id="default"):
        self.prompts.append(prompt)
        return self.answer

    async def _get_memory(self, u):
        class M:
            async def clear(self):
                pass
        return M()


class _Chat:
    def __init__(self):
        self.posts = []

    async def post_as_agent(self, channel, agent, text, meta):
        self.posts.append((channel, agent, text, meta["job_id"]))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated HOME + a Web UI job runner with two agents."""
    from myclaw.jobs.runner import JobRunner
    from myclaw.web import jobs_api

    monkeypatch.setenv("HOME", str(tmp_path))
    agents = {"default": _Agent("default"), "Coder": _Agent("Coder", "Tests pass.")}
    chat = _Chat()
    runner = JobRunner(lambda: agents, lambda: chat)
    monkeypatch.setattr(jobs_api, "_runner", runner)
    yield agents, chat, runner
    from myclaw.events import CURRENT_AGENT, CURRENT_CHANNEL

    while _TOKENS:  # undo _as() so later tests are not "in a channel"
        a, c = _TOKENS.pop()
        CURRENT_CHANNEL.reset(c)
        CURRENT_AGENT.reset(a)


_TOKENS: list = []


def _as(agent, channel=None):
    from myclaw.events import CURRENT_AGENT, CURRENT_CHANNEL

    _TOKENS.append((CURRENT_AGENT.set(agent), CURRENT_CHANNEL.set(channel)))


def test_agent_creates_a_job_that_reports_to_its_channel(env):
    from myclaw.jobs import store
    from myclaw.tools.jobs import job_create, job_list

    _as("default", "general")
    out = job_create("Nightly check", "Check the disk space and report.", agent="Coder",
                     schedule="daily", at_time="02:30")
    assert out.startswith("Created job job-") and "@Coder" in out and "#general" in out
    job = store.list_jobs()[0]
    assert job["agent"] == "Coder" and job["schedule"] == "daily" and job["at_time"] == "02:30"
    assert job["channel_id"] == "general" and job["created_by"] == "agent:default"
    assert job["next_run"] and job["next_run"] > time.time()
    listing = job_list()
    assert "Nightly check" in listing and "daily at 02:30" in listing and "by agent:default" in listing


def test_validation_errors_are_readable(env):
    from myclaw.tools.jobs import job_create

    _as("default")
    assert "no agent called 'Ghost'" in job_create("x", "y", agent="Ghost")
    assert "at least 5" in job_create("x", "y", schedule="interval", every_minutes=1)
    assert "say when" in job_create("x", "y", schedule="once", when="someday")
    assert "Error" in job_create("x", "", schedule="manual")


def test_once_parses_relative_and_clock_times(env):
    from myclaw.tools.jobs import _when

    now = time.time()
    assert 29 * 60 < _when("in 30 minutes") - now < 31 * 60
    assert 0 < _when("08:30") - now <= 86400
    assert _when("tomorrow 09:00") - now > 0
    assert _when("2030-01-02 03:04") > now
    assert _when("whenever") is None


def test_agent_runs_a_job_and_reads_the_result(env):
    from myclaw.jobs import store
    from myclaw.tools.jobs import job_create, job_status

    agents, chat, runner = env
    _as("default", "dev")
    job_create("Run tests", "Run the test suite and summarise.", agent="Coder")
    job = store.list_jobs()[0]
    # what job_run starts in the background, awaited here
    run_id = asyncio.run(runner.run_job(job["id"], trigger="agent"))
    run = store.get_run(run_id)
    assert run["status"] == "ok" and run["output"] == "Tests pass." and run["trigger"] == "agent"
    assert agents["Coder"].prompts == ["Run the test suite and summarise."]
    assert chat.posts == [("dev", "Coder", "Tests pass.", job["id"])]  # reported back to the channel
    st = job_status(job["id"])
    assert "· ok ·" in st and "Tests pass." in st and "trigger agent" in st
    assert store.get_job(job["id"])["last_status"] == "ok"


def test_job_run_tool_starts_in_background(env, monkeypatch):
    from myclaw.tools.jobs import job_create, job_run

    _, _, runner = env
    started = []
    monkeypatch.setattr(runner, "run_in_background", lambda jid, trigger="manual": started.append((jid, trigger)))
    _as("default")
    job_create("Ping", "Say pong.")
    from myclaw.jobs import store

    jid = store.list_jobs()[0]["id"]
    assert job_run("Ping").startswith("Started job") and started == [(jid, "agent")]
    assert "no job" in job_run("nope")


def test_job_update_pause_and_ownership(env):
    from myclaw.jobs import store
    from myclaw.tools.jobs import job_create, job_update

    _as("default")
    job_create("Mine", "agent job", schedule="interval", every_minutes=30)
    users = store.create_job({"name": "Users", "prompt": "user job"})  # made by the user
    mine = [j for j in store.list_jobs() if j["name"] == "Mine"][0]
    assert "PAUSED" in job_update(mine["id"], state="pause")
    assert store.get_job(mine["id"])["enabled"] is False or store.get_job(mine["id"])["enabled"] == 0
    assert "every 60 min" in job_update(mine["id"], state="resume", every_minutes=60)
    assert "only pause or resume" in job_update(users["id"], prompt="changed")
    assert "Updated" in job_update(users["id"], state="pause")


def test_switch_off_job_creation(env, monkeypatch):
    from myclaw.config import AppConfig
    from myclaw.tools.jobs import job_create

    cfg = AppConfig()
    cfg.agents.allow_job_creation = False
    monkeypatch.setattr("myclaw.config.load_config", lambda *a, **k: cfg)
    assert "may not create" in job_create("x", "y")


def test_legacy_schedule_becomes_a_job_in_the_web_ui(env):
    from myclaw.jobs import store
    from myclaw.tools.scheduler import nlp_schedule, schedule

    _as("Coder", "dev")
    out = schedule("Remind Adrian to stretch", delay=600)
    assert out.startswith("Created job") and "once at" in out
    out = nlp_schedule("Check the backups", "every 2 hours")
    assert "every 120 min" in out
    names = {j["name"]: j for j in store.list_jobs()}
    assert names["Remind Adrian to stretch"]["agent"] == "Coder"
    assert names["Check the backups"]["schedule"] == "interval"


def test_tools_registered_and_agents_told():
    from myclaw.agent_team import team_section
    from myclaw.config import AppConfig
    from myclaw.tools import TOOLS

    for name in ("job_create", "job_list", "job_run", "job_status", "job_update"):
        assert name in TOOLS
    assert "job_create" in team_section(AppConfig(), "default")
