"""Prior-run context (2026-11-25): a job run sees a bounded, sanitized summary
of its last N finished runs (job field ``context_runs``, default 0), never its
own run and never secrets/PII."""
import asyncio

import pytest


class _Agent:
    def __init__(self, name, answers=None):
        self.name, self.model, self._provider_name = name, "m", "p"
        self.answers = list(answers or ["done"])
        self.prompts = []

    async def think(self, prompt, user_id="default"):
        self.prompts.append(prompt)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    async def _get_memory(self, u):
        class M:
            async def clear(self):
                pass
        return M()


@pytest.fixture
def env(tmp_path, monkeypatch):
    from myclaw.jobs.runner import JobRunner

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    agents = {"default": _Agent("default")}
    runner = JobRunner(lambda: agents, lambda: None)

    async def no_review(run_id):
        return None

    from myclaw.jobs import medic
    monkeypatch.setattr(medic, "review_run", no_review)
    yield agents, runner


def _run(runner, job_id):
    return asyncio.run(runner.run_job(job_id))


def test_prior_run_context_reaches_the_agent(env):
    from myclaw.jobs import store

    agents, runner = env
    job = store.create_job({"name": "Ctx", "prompt": "Do the thing.", "context_runs": 3})
    first = _run(runner, job["id"])
    second = _run(runner, job["id"])
    assert first != second
    prompt = agents["default"].prompts[1]
    assert "background" in prompt and f"Run #{first}" in prompt
    assert "ok" in prompt and "done" in prompt  # status + bounded output of run 1
    assert prompt.endswith("Do the thing.")


def test_current_run_is_never_its_own_context(env):
    from myclaw.jobs import store

    agents, runner = env
    job = store.create_job({"name": "Self", "prompt": "unique-prompt"})
    run_id = _run(runner, job["id"])
    prompt = agents["default"].prompts[0]
    assert f"Run #{run_id}" not in prompt
    assert "background" not in prompt  # no finished runs yet at all


def test_context_runs_zero_disables_and_n_limits(env):
    from myclaw.jobs import store

    agents, runner = env
    off = store.create_job({"name": "Off", "prompt": "p"})
    _run(runner, off["id"])
    _run(runner, off["id"])
    assert "background" not in agents["default"].prompts[-1]

    one = store.create_job({"name": "One", "prompt": "p", "context_runs": 1})
    ids = [_run(runner, one["id"]) for _ in range(3)]
    prompt = agents["default"].prompts[-1]
    assert f"Run #{ids[1]}" in prompt and f"Run #{ids[0]}" not in prompt


def test_context_is_bounded(env):
    from myclaw.jobs import runner as runner_mod
    from myclaw.jobs import store

    agents, runner = env
    job = store.create_job({"name": "Big", "prompt": "p", "context_runs": 3})
    run_id = store.start_run(job["id"], "run", "manual")
    store.finish_run(run_id, "ok", "x" * 50_000)
    _run(runner, job["id"])
    prompt = agents["default"].prompts[-1]
    assert len(prompt) < runner_mod.PRIOR_CONTEXT_MAX_CHARS + 100
    assert "truncated" in prompt


def test_secrets_and_pii_never_reach_the_prompt(env):
    from myclaw.jobs import store

    agents, runner = env
    job = store.create_job({"name": "Leak", "prompt": "p", "context_runs": 3})
    run_id = store.start_run(job["id"], "run", "manual")
    store.finish_run(run_id, "error", "", "failed: api_key = sk-abcdef1234567890 leaked")
    _run(runner, job["id"])
    prompt = agents["default"].prompts[-1]
    assert "sk-abcdef1234567890" not in prompt
    assert "Error:" in prompt  # the failure is still reported, sanitized


@pytest.mark.parametrize("token", ["ghp_" + "a" * 36, "github_pat_" + "b" * 40])
def test_github_tokens_do_not_reach_prior_context(env, token):
    from myclaw.jobs import store

    agents, runner = env
    job = store.create_job({"name": "Token", "prompt": "p", "context_runs": 1})
    run_id = store.start_run(job["id"], "run", "manual")
    store.finish_run(run_id, "ok", f"Deployed using {token} successfully.")
    _run(runner, job["id"])
    assert token not in agents["default"].prompts[-1]
    assert "[REDACTED]" in agents["default"].prompts[-1]


def test_score_and_medic_review_summary_are_included(env):
    from myclaw.jobs import store

    agents, runner = env
    job = store.create_job({"name": "Reviewed", "prompt": "p", "context_runs": 3})
    run_id = store.start_run(job["id"], "run", "manual")
    store.finish_run(run_id, "ok", "fine")
    store.set_evaluation(run_id, 7, {"score": 7, "verdict": "pass", "reasons": "good"})
    store.save_medic_review(run_id, job["id"], {"severity": "info", "summary": "all tools behaved"})
    _run(runner, job["id"])
    prompt = agents["default"].prompts[-1]
    assert "score 7/10" in prompt and "Medic review: all tools behaved" in prompt


def test_retry_attempt_sees_the_failed_attempt(env):
    from myclaw.jobs import store

    agents, runner = env
    agents["default"].answers = ["Sorry, the LLM service is unavailable right now.", "recovered"]
    job = store.create_job({"name": "Flaky", "prompt": "p", "retries": 1, "context_runs": 3})
    last = _run(runner, job["id"])
    runs = sorted(store.list_runs(job["id"]), key=lambda r: r["id"])
    assert [r["status"] for r in runs] == ["error", "ok"] and last == runs[1]["id"]
    retry_prompt = agents["default"].prompts[1]
    assert f"Run #{runs[0]['id']}" in retry_prompt  # the failed attempt is context
    assert f"Run #{runs[1]['id']}" not in retry_prompt  # but not the retry itself


def test_context_runs_validated_and_editable(env):
    from myclaw.jobs import store

    job = store.create_job({"name": "Defaults", "prompt": "p"})
    assert job["context_runs"] == 0
    assert store.update_job(job["id"], {"context_runs": 5})["context_runs"] == 5
    with pytest.raises(ValueError, match="between 0 and 20"):
        store.update_job(job["id"], {"context_runs": 99})
