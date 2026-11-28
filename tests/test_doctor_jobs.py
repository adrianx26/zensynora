"""Doctor job planning, recoverable workflows and durable audit identity."""

import asyncio
import json
import sqlite3
import time
import uuid
from functools import wraps

import pytest


class Agent:
    def __init__(self, name):
        self.name, self.model, self._provider_name = name, "test", "test"
        self.description = f"{name} specialist"
        self.prompts = []
        self.contexts = []
        self.answers = ["Done."]

    async def think(self, prompt, user_id="webui"):
        from myclaw.jobs import store

        self.prompts.append(prompt)
        self.contexts.append((store.CURRENT_JOB_ID.get(), store.CURRENT_RUN_ID.get()))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    async def _get_memory(self, user_id):
        class Memory:
            async def clear(self):
                pass
        return Memory()


@pytest.fixture
def env(tmp_path, monkeypatch):
    from myclaw import provider
    from myclaw.config import AppConfig
    from myclaw.jobs import medic, store
    from myclaw.jobs.runner import JobRunner
    from myclaw.knowledge import storage

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    storage.set_knowledge_root(None)
    cfg = AppConfig.model_validate({"agents": {"named": [
        {"name": "Coder", "description": "Python code tests"},
        {"name": "Reviewer", "description": "Review reports"},
    ]}})
    monkeypatch.setattr("myclaw.config.load_config", lambda *args, **kwargs: cfg)
    agents = {name: Agent(name) for name in ("default", "Coder", "Reviewer")}
    runner = JobRunner(lambda: agents)
    monkeypatch.setattr(runner, "_defer_medic_review", lambda *args: None)
    monkeypatch.setattr(runner, "_defer_reflexion", lambda *args: None)
    monkeypatch.setattr("myclaw.web.jobs_api._runner", runner)

    @wraps(provider.complete_with_fallback)
    async def completion(*args, **kwargs):
        return json.dumps({"agent": "Coder", "reason": "Python specialist", "team_agents": [], "subtasks": []}), "test", "test"

    async def diagnosis(run_id, **kwargs):
        result = {"cause": "Temporary failure", "category": "unknown", "job_changes": {}, "manual_steps": []}
        store.set_diagnosis(run_id, result)
        return result

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", completion)
    monkeypatch.setattr(medic, "diagnose", diagnosis)
    yield store, cfg, agents, runner
    storage.set_knowledge_root(None)


def test_migrates_legacy_ids_and_retains_complete_audit_snapshots(env):
    store, _, _, _ = env
    path = store.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = store._new_job({"id": "legacy", "name": "Old job", "prompt": "old task"})
    with sqlite3.connect(path) as conn:
        conn.executescript(store._SCHEMA)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]
        conn.execute(f"INSERT INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     store._values(legacy, cols))
        conn.execute("INSERT INTO runs (job_id, kind, trigger, status, started) VALUES ('legacy','run','manual','ok',0)")
    job = store.get_job("legacy")
    old_run = store.list_runs("legacy")[0]
    assert job["id"] == "legacy" and old_run["id"] == 1
    assert uuid.UUID(job["uid"]) != uuid.UUID(old_run["uid"])
    assert store.get_job("legacy")["uid"] == job["uid"]
    full = "unique end marker " + "x" * 20_000 + " END"
    store.update_job("legacy", {"prompt": full})
    store.update_job("legacy", {"prompt": "new task"})
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT before_json FROM job_audit WHERE action='updated' ORDER BY created DESC").fetchone()[0]
    assert json.loads(before)["prompt"] == full
    audit = store.job_audit("legacy")
    assert "before_json" not in audit[0]
    assert store.get_job("legacy")["uid"] == job["uid"]


def test_agent_creation_is_unassigned_deduplicated_and_planned_one_at_a_time(env):
    from myclaw.tools.jobs import job_create

    store, cfg, _, runner = env
    cfg.medic.plan_jobs = True
    first = job_create("Python task", "Run Python tests.", run_now=True)
    assert "unassigned" in first
    jobs = store.list_jobs()
    job = jobs[0]
    assert job["agent"] == "" and job["planning_state"] == "pending"
    assert store.due_jobs() == []
    assert "already exists" in job_create("Python task", "Run Python tests.")
    other = store.create_job({"name": "Other", "prompt": "Review results", "doctor_managed": True})
    asyncio.run(runner._triage_one())
    assert store.get_job(job["id"])["agent"] == "Coder"
    assert store.get_job(other["id"])["planning_state"] == "pending"
    assert store.due_jobs()[0]["id"] == job["id"]


def test_plan_rejects_stale_edits_instead_of_overwriting_user_data(env, monkeypatch):
    from myclaw.jobs import planning

    store, _, _, runner = env
    job = store.create_job({"name": "Race", "prompt": "Old prompt", "doctor_managed": True})

    async def completion(*args, **kwargs):
        store.update_job(job["id"], {"prompt": "User's new prompt"})
        return '{"agent":"Coder","reason":"choose coder"}', "test", "test"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", completion)
    with pytest.raises(ValueError, match="changed during"):
        asyncio.run(planning.plan_job(job["id"], runner._registry()))
    assert store.get_job(job["id"])["prompt"] == "User's new prompt"
    assert store.children(job["id"]) == []


def test_approval_creates_linked_subtasks_and_runs_the_workflow(env):
    from myclaw.jobs import planning

    store, cfg, agents, runner = env
    job = store.create_job({"name": "Report", "prompt": "Build a report", "doctor_managed": True})
    raw = {"agent": "default", "reason": "Ordered workflow", "subtasks": [
        {"name": "Inspect", "prompt": "Inspect source", "agents": ["Coder"]},
        {"name": "Report", "prompt": "Prepare report", "agents": ["Reviewer"]},
    ]}
    plan = planning.validate_plan(raw, job, agents, cfg)
    proposed = store.save_plan(job["id"], plan, job["updated"])
    assert proposed["planning_state"] == "needs_approval"
    assert store.children(job["id"]) == []
    approved = asyncio.run(planning.approve(job["id"], plan["id"], agents))
    assert approved["planning_state"] == "ready"
    children = store.children(job["id"])
    assert len({child["uid"] for child in children}) == 2
    run_id = asyncio.run(runner.run_job(job["id"]))
    assert store.get_run(run_id)["status"] == "ok"
    linked = store.linked_runs(run_id)
    assert {run["job_id"] for run in linked} == {child["id"] for child in children}
    assert len({run["uid"] for run in [store.get_run(run_id), *linked]}) == 3
    assert "quoted information" in agents["Reviewer"].prompts[0]
    assert agents["Coder"].contexts == [(children[0]["id"], linked[0]["id"])]
    with pytest.raises(ValueError, match="stale"):
        asyncio.run(planning.approve(job["id"], plan["id"], agents))


def test_team_size_counts_primary_and_graveyard_cannot_execute(env, monkeypatch):
    from myclaw import evolve
    from myclaw.jobs import planning

    store, cfg, agents, runner = env
    cfg.medic.job_max_team = 2
    job = store.create_job({"name": "Team", "prompt": "work", "agent": "Coder"})
    with pytest.raises(ValueError, match="team"):
        planning.validate_plan({"agent": "default", "team_agents": ["Coder", "Reviewer"]}, job, agents, cfg)
    monkeypatch.setattr(evolve, "_read_trash", lambda: [{"agent": {"name": "Coder"}, "state": "graveyard"}])
    assert evolve.fusion_blocks_agent("coder")
    assert "Coder" not in planning.available(agents)
    run_id = asyncio.run(runner.run_job(job["id"]))
    assert store.get_run(run_id)["status"] == "blocked"
    assert not agents["Coder"].prompts and not agents["default"].prompts


@pytest.mark.parametrize("eventual_ok", [True, False])
def test_doctor_recovery_changes_agent_and_obeys_attempt_limit(env, monkeypatch, eventual_ok):
    store, _, _, runner = env
    job = store.create_job({"name": "Recover", "prompt": "Python tests", "doctor_recovery": True,
                            "recovery_attempts": 2})
    seen = []

    async def execute(job, run_id, **kwargs):
        seen.append(job["agent"])
        status = "ok" if eventual_ok and len(seen) == 3 else "error"
        store.finish_run(run_id, status, "Done" if status == "ok" else "", "temporary" if status != "ok" else "")
        return status

    monkeypatch.setattr(runner, "_execute", execute)
    run_id = asyncio.run(runner.run_job(job["id"]))
    assert seen == ["default", "Coder", "Coder"]
    assert store.get_job(job["id"])["recovery_state"] == ("ok" if eventual_ok else "limit")
    assert store.get_run(run_id)["status"] == ("ok" if eventual_ok else "error")
    assert store.get_run(run_id)["parent_run_id"] is not None
    assert len({run["uid"] for run in store.list_runs(job["id"])}) == 3


def test_retry_investigates_original_failure_and_can_be_stopped(env, monkeypatch):
    from myclaw.jobs import medic

    store, _, _, runner = env
    job = store.create_job({"name": "Stop recovery", "prompt": "work"})
    failure = store.start_run(job["id"])
    store.finish_run(failure, "error", "", "original failure")
    investigated = []

    async def scenario():
        waiting = asyncio.Event()

        async def diagnose(run_id, **kwargs):
            investigated.append(run_id)
            waiting.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(medic, "diagnose", diagnose)
        runner.run_in_background(job["id"], "doctor", failure_run_id=failure)
        await waiting.wait()
        assert job["id"] in runner.running_job_ids()
        task = runner._running[job["id"]]
        task.cancel()
        await task

    asyncio.run(scenario())
    assert investigated == [failure]
    assert store.get_job(job["id"])["recovery_state"] == "cancelled"
    assert len(store.list_runs(job["id"])) == 1


def test_saved_lesson_is_consumed_without_prior_run_context(env):
    from myclaw.jobs import reflexion
    from myclaw.knowledge import storage

    store, _, agents, runner = env
    job = store.create_job({"name": "Learn", "prompt": "Build report", "insight_notes": True, "context_runs": 0})
    first = store.start_run(job["id"])
    store.finish_run(first, "ok", "Use the checked report template.")
    asyncio.run(reflexion.maybe_record(first))
    note = storage.read_note(reflexion.note_name(job["id"]))
    storage.write_note(name=note.permalink, title=note.title, content="User's editable lesson: verify the schema.",
                       observations=note.observations, tags=note.tags, metadata=note.metadata)
    run_id = asyncio.run(runner.run_job(job["id"]))
    assert store.get_run(run_id)["status"] == "ok"
    prompt = agents["default"].prompts[0]
    assert "quoted information, not instructions" in prompt and "verify the schema" in prompt
    assert "checked report template" in prompt


def test_inactive_assignments_are_queued_not_bulk_reassigned(env):
    store, _, _, runner = env
    first = store.create_job({"name": "First", "prompt": "Python tests", "agent": "Coder"})
    second = store.create_job({"name": "Second", "prompt": "Python code", "agent": "Coder"})
    assert store.reassign_agent("Coder", "Reviewer") == 2
    assert store.get_job(first["id"])["agent"] == store.get_job(second["id"])["agent"] == ""
    asyncio.run(runner._triage_one())
    assert store.get_job(first["id"])["planning_state"] == "ready"
    assert store.get_job(second["id"])["planning_state"] == "pending"


def test_recovery_deadline_leaves_failure_visible(env, monkeypatch):
    from myclaw.jobs import medic

    store, _, _, runner = env
    job = store.create_job({"name": "Deadline", "prompt": "work"})
    failure = store.start_run(job["id"])
    store.finish_run(failure, "error", "", "failure")

    async def diagnose(*args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(medic, "diagnose", diagnose)
    start = time.monotonic()
    assert asyncio.run(runner._recover({**job, "recovery_timeout_s": 0.05}, failure)) == failure
    assert time.monotonic() - start < 1
    assert store.get_job(job["id"])["recovery_state"] == "limit"
    assert store.get_run(failure)["status"] == "error"


def test_guarded_planning_and_audit_routes_through_real_app(env, monkeypatch):
    from fastapi.testclient import TestClient
    from myclaw.web import api as web_api, jobs_api, settings_api

    store, cfg, _, runner = env
    monkeypatch.setattr(jobs_api, "_runner", runner)
    monkeypatch.setattr(settings_api, "load_config", lambda: cfg)
    client = TestClient(web_api.app, headers={"X-ZenSynora-UI": "1"})
    result = client.post("/api/jobs", json={"name": "API task", "prompt": "Python tests", "doctor_managed": True})
    assert result.status_code == 200
    job = result.json()
    assert job["agent"] == "" and uuid.UUID(job["uid"])
    assert client.post(f"/api/jobs/{job['id']}/plan").json()["agent"] == "Coder"
    audit = client.get(f"/api/jobs/{job['id']}/audit").json()
    assert any(event["action"] == "plan-applied" for event in audit["events"])
    failure = store.start_run(job["id"])
    store.finish_run(failure, "error", "", "failure")
    started = []
    monkeypatch.setattr(runner, "run_in_background", lambda *args, **kwargs: started.append((args, kwargs)))
    response = client.post(f"/api/jobs/runs/{failure}/retry-assisted")
    assert response.status_code == 200 and started[0][1]["failure_run_id"] == failure
    remote = TestClient(web_api.app, client=("203.0.113.8", 1234))
    assert remote.get(f"/api/jobs/{job['id']}/audit").status_code == 403
    assert client.post(f"/api/jobs/{job['id']}/plan", headers={"Origin": "https://foreign.example"}).status_code == 403


def test_pending_jobs_do_not_start_a_planner_when_feature_is_off(env):
    store, cfg, _, runner = env
    cfg.medic.plan_jobs = False
    store.create_job({"name": "Wait", "prompt": "work", "doctor_managed": True})

    async def scenario():
        tick = asyncio.Event()
        runner.on_tick.append(tick.set)
        task = asyncio.create_task(runner._loop())
        await tick.wait()
        assert runner._planning_task is None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_team_members_have_separate_tracked_runs(env):
    store, _, agents, runner = env
    job = store.create_job({"name": "Team task", "prompt": "Prepare report", "team_agents": ["Reviewer"]})
    root = asyncio.run(runner.run_job(job["id"]))
    linked = store.linked_runs(root)
    assert [run["agent"] for run in linked] == ["default", "Reviewer"]
    assert len({run["uid"] for run in linked}) == 2
    assert "Do not repeat completed external actions" in agents["Reviewer"].prompts[0]
    assert store.get_run(root)["status"] == "ok"


def test_assisted_success_requires_a_passing_judge(env, monkeypatch):
    store, _, _, runner = env
    job = store.create_job({"name": "Criteria", "prompt": "Report", "doctor_recovery": True,
                            "success_criteria": "Verified correct", "recovery_attempts": 1})

    async def judge(run_id, **kwargs):
        return {"score": 2, "verdict": "fail", "reasons": "Incorrect report"}

    monkeypatch.setattr(runner, "evaluate_run", judge)
    run_id = asyncio.run(runner.run_job(job["id"]))
    assert store.get_run(run_id)["status"] == "error"
    assert "Success criteria" in store.get_run(run_id)["error"]
    assert store.get_job(job["id"])["recovery_state"] == "limit"


def _planned_parent(env):
    from myclaw.jobs import planning

    store, cfg, agents, _ = env
    job = store.create_job({"name": "Workflow", "prompt": "Inspect and report",
                            "doctor_managed": True, "doctor_auto_workflow": True})
    plan = planning.validate_plan({"agent": "default", "reason": "Ordered steps", "subtasks": [
        {"name": "Inspect", "prompt": "Read source", "agents": ["Coder"]},
        {"name": "Report", "prompt": "Write report", "agents": ["Reviewer"]},
    ]}, job, agents, cfg)
    return store.save_plan(job["id"], plan, job["updated"]), plan


def test_reordered_plan_keeps_child_ids_but_changes_execution_order(env):
    store, _, _, runner = env
    job, plan = _planned_parent(env)
    old_ids = {child["name"]: child["id"] for child in store.children(job["id"])}
    plan["subtasks"].reverse()
    store.save_plan(job["id"], plan, job["updated"])
    ordered = store.children(job["id"])
    assert [child["name"] for child in ordered] == ["Report", "Inspect"]
    assert {child["name"]: child["id"] for child in ordered} == old_ids
    root = asyncio.run(runner.run_job(job["id"]))
    assert [run["agent"] for run in store.linked_runs(root)] == ["Reviewer", "Coder"]


def test_parent_edit_invalidates_plan_and_manual_opt_out_stops_old_workflow(env):
    store, _, agents, runner = env
    job, _ = _planned_parent(env)
    updated = store.update_job(job["id"], {"prompt": "User's new request"})
    assert updated["planning_state"] == "pending" and not updated["agent"]
    manual = store.update_job(job["id"], {"doctor_managed": False, "agent": "Reviewer"})
    assert manual["planning_state"] == "ready"
    assert store.children(job["id"]) == []
    root = asyncio.run(runner.run_job(job["id"]))
    assert store.get_run(root)["status"] == "ok"
    assert agents["Reviewer"].prompts == ["User's new request"]
    assert not agents["Coder"].prompts


def test_stale_analysis_failure_does_not_block_a_new_manual_assignment(env, monkeypatch):
    store, _, _, runner = env
    job = store.create_job({"name": "Stale", "prompt": "old task", "doctor_managed": True})

    async def completion(*args, **kwargs):
        store.update_job(job["id"], {"agent": "Reviewer", "doctor_managed": False})
        return '{"agent":"Coder","reason":"old analysis"}', "test", "test"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", completion)
    asyncio.run(runner._triage_one())
    current = store.get_job(job["id"])
    assert current["agent"] == "Reviewer" and current["planning_state"] == "ready"
    assert store.job_audit(job["id"])[-1]["action"] == "planning-discarded"


def test_parent_deletion_retains_children_paused_with_history_and_audit(env):
    store, _, _, _ = env
    job, _ = _planned_parent(env)
    child = store.children(job["id"])[0]
    run = store.start_run(child["id"])
    store.finish_run(run, "ok", "Historical result")
    assert store.delete_job_if_idle(job["id"]) == "deleted"
    retained = store.get_job(child["id"])
    assert retained["uid"] == child["uid"] and not retained["enabled"] and retained["parent_job_id"] == ""
    assert store.get_run(run)["output"] == "Historical result"
    deleted = store.job_audit(job["id"])[-1]
    assert deleted["action"] == "deleted" and deleted["before"]["uid"] == job["uid"]
    assert deleted["after"] is None


def test_workflow_retry_reuses_completed_steps_without_repeating_actions(env):
    store, _, agents, runner = env
    job, _ = _planned_parent(env)
    agents["Reviewer"].answers = ["[Error temporary failure", "Done."]
    first = asyncio.run(runner.run_job(job["id"]))
    assert store.get_run(first)["status"] == "error"
    second = asyncio.run(runner.run_job(job["id"], "doctor-step", first))
    assert store.get_run(second)["status"] == "ok"
    assert len(agents["Coder"].prompts) == 1 and len(agents["Reviewer"].prompts) == 2
    reused = store.linked_runs(second)[0]
    assert reused["status"] == "ok" and "Reused completed step" in reused["error"]
    assert reused["uid"] != store.linked_runs(first)[0]["uid"]


def test_atomic_idle_guard_prevents_edits_and_deletes_with_an_active_child(env):
    store, _, _, _ = env
    job, _ = _planned_parent(env)
    child = store.children(job["id"])[0]
    run = store.start_run(child["id"])
    with pytest.raises(ValueError, match="Stop the active"):
        store.update_job(job["id"], {"prompt": "changed"}, require_idle=True)
    assert store.delete_job_if_idle(job["id"]) == "running"
    assert store.get_job(job["id"])["prompt"] == job["prompt"]
    store.finish_run(run, "cancelled")


def test_team_member_retry_resumes_parent_and_deduplicates_completed_recovery(env, monkeypatch):
    store, _, agents, runner = env
    job = store.create_job({"name": "Cooperate", "prompt": "Prepare report", "team_agents": ["Reviewer"]})
    agents["Reviewer"].answers = ["[Error temporary failure", "Done."]

    async def completion(*args, **kwargs):
        return '{"agent":"default","team_agents":["Reviewer"]}', "test", "test"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", completion)
    first = asyncio.run(runner.run_job(job["id"]))
    failed_step = store.linked_runs(first)[1]
    assert failed_step["kind"] == "step" and failed_step["status"] == "error"
    second = asyncio.run(runner.retry_failed(failed_step["id"]))
    assert store.get_run(second)["status"] == "ok"
    assert len(agents["default"].prompts) == 1 and len(agents["Reviewer"].prompts) == 2
    before = len(store.list_runs(job["id"]))
    assert asyncio.run(runner.retry_failed(failed_step["id"])) == second
    assert len(store.list_runs(job["id"])) == before
    assert store.list_jobs()[0]["stats"]["runs"] == 2  # team-member steps do not inflate job success statistics


def test_changed_subtask_prompt_does_not_reuse_an_old_result(env):
    store, _, agents, runner = env
    job, _ = _planned_parent(env)
    agents["Reviewer"].answers = ["[Error temporary failure", "Done."]
    first = asyncio.run(runner.run_job(job["id"]))
    child = store.children(job["id"])[0]
    store.update_job(child["id"], {"prompt": "Read the changed source"})
    second = asyncio.run(runner.run_job(job["id"], "doctor-step", first))
    assert store.get_run(second)["status"] == "ok"
    assert agents["Coder"].prompts == ["Read source", "Read the changed source"]


def test_audit_uuid_is_not_misclassified_as_a_phone_number(env):
    store, _, _, _ = env
    value = "1e603350-5404-4343-a21e-c17e309b6bd4"
    clean = store._safe_value({"uid": value, "text": "Call +40712345678 or 0712345678"}, preserve_ids=True)
    assert clean["uid"] == value and "+40712345678" not in clean["text"] and "0712345678" not in clean["text"]


def test_workflow_retry_invalidates_results_when_preceding_outputs_change(env):
    store, _, agents, runner = env
    job, plan = _planned_parent(env)
    plan["subtasks"].append({"name": "Publish", "prompt": "Publish report", "agents": ["default"], "tools": []})
    store.save_plan(job["id"], plan, job["updated"])
    agents["Coder"].answers = ["original source", "changed source"]
    agents["default"].answers = ["[Error temporary failure", "Done."]
    first = asyncio.run(runner.run_job(job["id"]))
    assert store.get_run(first)["status"] == "error"
    child = store.children(job["id"])[0]
    store.update_job(child["id"], {"prompt": "Read changed source"})
    second = asyncio.run(runner.run_job(job["id"], "doctor-step", first))
    assert store.get_run(second)["status"] == "ok"
    assert len(agents["Reviewer"].prompts) == 2
    assert "changed source" in agents["Reviewer"].prompts[-1]


def test_run_audit_retains_complete_results_evaluation_and_diagnosis(env):
    store, _, _, _ = env
    job = store.create_job({"name": "Auditable result", "prompt": "Write report"})
    prompt = "Quoted prior results; write the remaining report"
    run = store.start_run(job["id"], provider="test", model="test", execution_prompt=prompt)
    output = "result " + "x" * 20_000 + " END"
    store.finish_run(run, "ok", output)
    store.set_evaluation(run, 0.9, {"reason": "Useful report"})
    store.set_diagnosis(run, {"cause": "No failure"})
    with sqlite3.connect(store.db_path()) as conn:
        snapshots = {action: (json.loads(before) if before else None, json.loads(after))
                     for action, before, after in conn.execute(
                         "SELECT action, before_json, after_json FROM job_audit WHERE run_id = ?", (run,))}
    assert snapshots["run-started"][1]["model"] == "test"
    assert snapshots["run-started"][1]["execution_prompt"] == prompt
    before, after = snapshots["run-finished"]
    assert before["status"] == "running" and after["output"] == output
    assert after["id"] == run and after["uid"] == store.get_run(run)["uid"]
    assert snapshots["run-evaluated"][1]["evaluation"] == {"reason": "Useful report"}
    assert snapshots["run-diagnosed"][1]["diagnosis"] == {"cause": "No failure"}
    active = store.start_run(job["id"])
    store.progress_run(active, "Partial progress")
    assert store.recover_interrupted() == 1
    event = store.job_audit(job["id"])[-1]
    assert event["action"] == "run-interrupted" and event["after"]["uid"] == store.get_run(active)["uid"]
    assert event["before"]["status"] == "running" and event["after"]["status"] == "interrupted"
    assert event["after"]["output"] == "Partial progress"


def test_fallback_fits_each_actual_model_request(env, monkeypatch):
    from myclaw import llm_errors, provider
    from myclaw.agent import Agent as RealAgent

    _, cfg, _, _ = env
    cfg.agents.defaults.provider = "ollama"
    cfg.agents.defaults.model = "primary"
    cfg.agents.rotation = type(cfg.agents.rotation).model_validate({
        "enabled": True, "entries": [{"provider": "ollama", "model": "fallback"}],
    })
    observed = []
    monkeypatch.setattr(llm_errors, "MODEL_COOLDOWN", {})
    monkeypatch.setattr(provider, "get_provider", lambda *args, **kwargs: object())

    async def fitted(self, provider_name, model, messages, send):
        observed.append((provider_name, model))
        return await send([{"role": "user", "content": f"fitted:{model}"}])

    async def complete(prov, messages, model):
        assert messages[0]["content"] == f"fitted:{model}"
        if model == "primary":
            raise RuntimeError("rate limit")
        return "Done."

    monkeypatch.setattr(RealAgent, "_fitted", fitted)
    monkeypatch.setattr(provider, "complete_text", complete)
    actual = provider.complete_with_fallback.__wrapped__
    result = asyncio.run(actual(cfg, [{"role": "user", "content": "large request"}], fitted=True))
    assert result == ("Done.", "ollama", "fallback")
    assert observed == [("ollama", "primary"), ("ollama", "fallback")]


@pytest.mark.parametrize("automatic", [False, True])
def test_assisted_team_recovery_can_reorganize_the_workflow_with_permission(env, automatic):
    store, _, agents, runner = env
    job = store.create_job({"name": "Recover team", "prompt": "Prepare report", "team_agents": ["Reviewer"],
                            "doctor_recovery": True, "doctor_auto_workflow": automatic})
    agents["Reviewer"].answers = ["[Error temporary failure"]
    first = asyncio.run(runner.run_job(job["id"]))
    current = store.get_job(job["id"])
    if not automatic:
        assert current["planning_state"] == "needs_approval" and current["recovery_state"] == "needs_approval"
        assert not agents["Coder"].prompts
        store.save_plan(job["id"], current["doctor_plan"], current["updated"], approved=True)
        first = asyncio.run(runner.retry_failed(first))
    assert store.get_run(first)["status"] == "ok"
    assert store.get_job(job["id"])["agent"] == "Coder" and not store.get_job(job["id"])["team_agents"]
    assert len(agents["Reviewer"].prompts) == 1 and len(agents["Coder"].prompts) == 1


def test_unchanged_approved_team_recovers_without_another_approval_or_repeated_actions(env, monkeypatch):
    from myclaw.jobs import planning

    store, cfg, agents, runner = env
    job = store.create_job({"name": "Approved team", "prompt": "Prepare report",
                            "doctor_managed": True, "doctor_recovery": True})
    raw = {"agent": "default", "team_agents": ["Reviewer"], "reason": "Review the result"}
    plan = planning.validate_plan(raw, job, agents, cfg)
    store.save_plan(job["id"], plan, job["updated"], approved=True)
    agents["Reviewer"].answers = ["[Error temporary failure", "Done."]

    async def completion(*args, **kwargs):
        return json.dumps(raw), "test", "test"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", completion)
    run = asyncio.run(runner.run_job(job["id"]))
    assert store.get_run(run)["status"] == "ok"
    assert store.get_job(job["id"])["planning_state"] == "ready"
    assert len(agents["default"].prompts) == 1 and len(agents["Reviewer"].prompts) == 2


def test_recovery_uses_live_subtask_edits_and_requires_approval_before_replacing_them(env, monkeypatch):
    store, _, agents, runner = env
    job, old_plan = _planned_parent(env)
    store.update_job(job["id"], {"doctor_auto_workflow": False})
    agents["Reviewer"].answers = ["[Error temporary failure"]
    first = asyncio.run(runner.run_job(job["id"]))
    child = store.children(job["id"])[0]
    store.update_job(child["id"], {"prompt": "Read the user-edited source"})
    evidence = []

    async def completion(cfg, messages, **kwargs):
        evidence.append(json.loads(messages[1]["content"]))
        return json.dumps({**old_plan, "requires_approval": False}), "test", "test"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", completion)
    assert asyncio.run(runner.retry_failed(first)) == first
    assert store.get_job(job["id"])["planning_state"] == "needs_approval"
    assert store.get_job(child["id"])["prompt"] == "Read the user-edited source"
    assert agents["Coder"].prompts == ["Read source"]
    assert evidence[0]["current_workflow"]["subtasks"][0]["prompt"] == "Read the user-edited source"


def test_editing_a_child_during_parent_analysis_discards_the_stale_plan(env, monkeypatch):
    from myclaw.jobs import planning

    store, _, agents, _ = env
    job, plan = _planned_parent(env)
    child = store.children(job["id"])[0]

    async def completion(*args, **kwargs):
        store.update_job(child["id"], {"prompt": "New user intent"})
        return json.dumps(plan), "test", "test"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", completion)
    with pytest.raises(ValueError, match="job changed"):
        asyncio.run(planning.plan_job(job["id"], agents))
    assert store.get_job(child["id"])["prompt"] == "New user intent"
    assert store.get_job(child["id"])["planning_state"] == "ready"


def test_approved_recovery_plan_executes_before_another_model_approval_request(env, monkeypatch):
    store, _, agents, runner = env
    job = store.create_job({"name": "Explicit approval", "prompt": "Prepare report", "team_agents": ["Reviewer"],
                            "doctor_recovery": True})
    agents["Reviewer"].answers = ["[Error temporary failure"]
    calls = []

    async def completion(*args, **kwargs):
        calls.append(True)
        return '{"agent":"Coder","requires_approval":true}', "test", "test"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", completion)
    first = asyncio.run(runner.run_job(job["id"]))
    current = store.get_job(job["id"])
    assert current["planning_state"] == "needs_approval"
    with pytest.raises(ValueError, match="Approve"):
        asyncio.run(runner.retry_failed(first))
    store.save_plan(job["id"], current["doctor_plan"], current["updated"], approved=True)
    second = asyncio.run(runner.retry_failed(first))
    assert store.get_run(second)["status"] == "ok"
    assert len(calls) == 1 and len(agents["Coder"].prompts) == 1
    assert "Previous attempt:" in agents["Coder"].prompts[0]
    assert "do not repeat completed external actions" in agents["Coder"].prompts[0]


def test_completed_recovery_is_not_reused_after_a_subtask_edit(env, monkeypatch):
    store, _, agents, runner = env
    job, _ = _planned_parent(env)
    agents["Reviewer"].answers = ["[Error temporary failure", "Done."]

    async def completion(*args, **kwargs):
        current = store.get_job(job["id"])
        raw = {"agent": current["agent"], "team_agents": current["team_agents"], "subtasks": [
            {"name": child["name"], "prompt": child["prompt"], "agents": [child["agent"], *child["team_agents"]]}
            for child in store.children(job["id"])]}
        return json.dumps(raw), "test", "test"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", completion)
    first = asyncio.run(runner.run_job(job["id"]))
    second = asyncio.run(runner.retry_failed(first))
    assert store.get_run(second)["status"] == "ok"
    child = store.children(job["id"])[0]
    store.update_job(child["id"], {"prompt": "Read a different source"})
    third = asyncio.run(runner.retry_failed(first))
    assert third != second and store.get_run(third)["status"] == "ok"
    assert agents["Coder"].prompts == ["Read source", "Read a different source"]


def test_adding_a_subtask_reviewer_reuses_the_completed_action_with_parent_context(env):
    store, _, agents, runner = env
    job, plan = _planned_parent(env)
    plan["subtasks"].append({"name": "Publish", "prompt": "Publish report", "agents": ["default"]})
    store.save_plan(job["id"], plan, job["updated"])
    agents["default"].answers = ["[Error temporary failure", "Done."]
    first = asyncio.run(runner.run_job(job["id"]))
    child = store.children(job["id"])[1]
    store.update_job(child["id"], {"team_agents": ["Coder"]})
    second = asyncio.run(runner.run_job(job["id"], "doctor-step", first))
    assert store.get_run(second)["status"] == "ok"
    assert len(agents["Reviewer"].prompts) == 1
    assert len(agents["Coder"].prompts) == 2
    assert "Review and improve" in agents["Coder"].prompts[-1]


@pytest.mark.parametrize("changes", [
    {"prompt": "Keep this new user edit"}, {"context_runs": 1}, {"insight_notes": True}, {"timeout_s": 600},
])
def test_approval_rejects_a_proposal_after_a_child_is_edited(env, monkeypatch, changes):
    from myclaw.jobs import planning

    store, _, agents, _ = env
    job, plan = _planned_parent(env)
    store.update_job(job["id"], {"doctor_auto_workflow": False})

    async def completion(*args, **kwargs):
        return json.dumps({**plan, "requires_approval": True}), "test", "test"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", completion)
    proposal = asyncio.run(planning.plan_job(job["id"], agents))
    child = store.children(job["id"])[0]
    store.update_job(child["id"], changes)
    with pytest.raises(ValueError, match="subtasks changed"):
        asyncio.run(planning.approve(job["id"], proposal["doctor_plan"]["id"], agents))
    current = store.get_job(job["id"])
    with pytest.raises(ValueError, match="subtasks changed"):
        store.save_plan(job["id"], current["doctor_plan"], current["updated"], approved=True)
    assert all(store.get_job(child["id"])[key] == value for key, value in changes.items())
    assert store.get_job(child["id"])["planning_state"] == "ready"


def test_actual_prior_context_is_recorded_and_invalidates_step_reuse(env):
    store, _, agents, runner = env
    job, _ = _planned_parent(env)
    child = store.children(job["id"])[0]
    store.update_job(child["id"], {"context_runs": 1})
    history = store.start_run(child["id"])
    store.finish_run(history, "ok", "Original historical guidance")
    agents["Reviewer"].answers = ["[Error temporary failure", "Done."]
    first = asyncio.run(runner.run_job(job["id"]))
    old_input = store.linked_runs(first)[0]
    assert "Original historical guidance" in old_input["execution_prompt"]
    assert old_input["execution_prompt"] == agents["Coder"].prompts[0]
    history = store.start_run(child["id"])
    store.finish_run(history, "ok", "New historical guidance")
    second = asyncio.run(runner.run_job(job["id"], "doctor-step", first))
    assert store.get_run(second)["status"] == "ok"
    new_input = store.linked_runs(second)[0]
    assert len(agents["Coder"].prompts) == 2
    assert "New historical guidance" in agents["Coder"].prompts[-1]
    assert new_input["execution_prompt"] == agents["Coder"].prompts[-1]
    assert new_input["input_hash"] != old_input["input_hash"]


def test_root_workflow_lessons_reach_subtasks_once_and_are_auditable(env):
    from myclaw.jobs import reflexion
    from myclaw.knowledge import storage

    store, _, agents, runner = env
    job, _ = _planned_parent(env)
    store.update_job(job["id"], {"insight_notes": True})
    history = store.start_run(job["id"])
    store.finish_run(history, "ok", "Use the checked report template.")
    asyncio.run(reflexion.maybe_record(history))
    note = storage.read_note(reflexion.note_name(job["id"]))
    storage.write_note(name=note.permalink, title=note.title, content="Root lesson: verify the schema.",
                       observations=note.observations, tags=note.tags, metadata=note.metadata)
    run = asyncio.run(runner.run_job(job["id"]))
    assert store.get_run(run)["status"] == "ok"
    prompt = agents["Coder"].prompts[0]
    assert prompt.count("Root lesson: verify the schema.") == 1
    assert store.linked_runs(run)[0]["execution_prompt"] == prompt


def test_workflow_context_failure_finishes_the_run_with_an_actionable_error(env, monkeypatch):
    store, _, _, runner = env
    job, _ = _planned_parent(env)

    async def broken_context(*args):
        raise ValueError("invalid saved lesson")

    monkeypatch.setattr(runner, "_execution_context", broken_context)
    run = asyncio.run(runner.run_job(job["id"]))
    result = store.get_run(run)
    assert result["status"] == "error" and "invalid saved lesson" in result["error"]
    assert "Review the job" in result["error"]
