"""Durable per-job insight (reflexion) notes (2026-11-25): opt-in per job,
one stable note per job updated in place, deterministic first with a bounded
optional model polish, sanitized outputs, idempotent per run, cancellable,
isolated per knowledge base."""
import asyncio

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated HOME (jobs.db) + isolated knowledge bases."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from myclaw.jobs import store
    from myclaw.knowledge import storage

    store._migrated.clear()
    storage.set_knowledge_root(None)
    yield store
    store._migrated.clear()
    storage.set_knowledge_root(None)


def _job(store, **extra):
    data = {"name": "Daily report", "prompt": "Summarize the day.", "insight_notes": True}
    data.update(extra)
    return store.create_job(data)


def _run(store, job_id, status="ok", output="All three reports are filed.", error=""):
    run_id = store.start_run(job_id, "run", "manual", "", "")
    store.finish_run(run_id, status, output, error, "test-provider", "test-model")
    return run_id


def _record(run_id, **kw):
    from myclaw.jobs import reflexion

    return asyncio.run(reflexion.maybe_record(run_id, **kw))


def _note(permalink, user_id="default"):
    from myclaw.knowledge import storage

    return storage.read_note(permalink, user_id)


def _facts(note):
    return [o for o in note.observations if o.category == "insight"]


def _kdir(tmp_path, base="default"):
    return tmp_path / ".myclaw" / "knowledge" / base


def test_opt_in_is_off_by_default(env, tmp_path):
    job = _job(env, insight_notes=False)
    run_id = _run(env, job["id"])
    assert _record(run_id) is None
    assert _note(f"job-insight-{job['id']}") is None


def test_successful_run_writes_one_stable_note(env, tmp_path):
    job = _job(env)
    run_id = _run(env, job["id"], output="Filed 3 reports, all green.")

    permalink = _record(run_id)
    assert permalink == f"job-insight-{job['id']}"
    note = _note(permalink)
    assert note.title == "Job insights: Daily report"
    assert {"jobs", "insights"} <= set(note.tags)
    assert note.metadata["type"] == "job-insight"
    assert note.metadata["origin"] == "agent"
    assert str(note.metadata["reflected_run"]) == str(run_id)
    facts = _facts(note)
    assert len(facts) == 1 and f"run {run_id}" in facts[0].content
    assert "Filed 3 reports, all green." in facts[0].content
    assert "test-provider/test-model" in note.content
    # pending-index markers are cleared once the write is indexed
    pending = _kdir(tmp_path) / ".pending-index"
    assert not pending.exists() or not list(pending.glob("*.json"))


def test_same_run_is_idempotent(env, tmp_path):
    job = _job(env)
    run_id = _run(env, job["id"])
    first = _record(run_id)
    second = _record(run_id)
    assert first == second
    assert len(_facts(_note(first))) == 1
    assert [p.name for p in _kdir(tmp_path).glob("*.md")] == [f"job-insight-{job['id']}.md"]


def test_new_run_updates_the_same_note_in_place(env, tmp_path):
    job = _job(env)
    first_run = _run(env, job["id"], output="First answer.")
    second_run = _run(env, job["id"], output="Second answer.")
    assert _record(first_run) == _record(second_run)
    note = _note(f"job-insight-{job['id']}")
    facts = _facts(note)
    assert len(facts) == 2
    assert f"run {first_run}" in facts[0].content  # old facts are kept
    assert f"run {second_run}" in facts[1].content
    assert str(note.metadata["reflected_run"]) == str(second_run)
    assert len(list(_kdir(tmp_path).glob("*.md"))) == 1


def test_cancelled_and_blocked_runs_are_skipped(env):
    job = _job(env)
    for status in ("cancelled", "blocked", "interrupted"):
        run_id = _run(env, job["id"], status=status, output="")
        assert _record(run_id) is None
    assert _note(f"job-insight-{job['id']}") is None


def test_plain_failure_records_nothing(env):
    job = _job(env)
    run_id = _run(env, job["id"], status="error", output="", error="boom")
    assert _record(run_id) is None
    assert _note(f"job-insight-{job['id']}") is None


def test_failure_with_diagnosis_records_a_lesson(env):
    job = _job(env)
    run_id = _run(env, job["id"], status="error", output="", error="needed more tool steps")
    env.set_diagnosis(run_id, {"cause": "The agent needed more tool steps than the job allows.",
                               "category": "tool_steps", "job_changes": {"max_steps": 16}})
    permalink = _record(run_id)
    note = _note(permalink)
    facts = _facts(note)
    assert len(facts) == 1
    assert "tool_steps" in facts[0].content and "more tool steps" in facts[0].content
    assert "max_steps" in facts[0].content  # the proposed fix, marked proposal-only
    assert "Proposed fix" in facts[0].content


def test_recurring_failures_record_a_lesson(env):
    job = _job(env)
    first = _run(env, job["id"], status="error", output="", error="boom 1")
    second = _run(env, job["id"], status="timeout", output="", error="too slow")
    assert _record(first) is None and _record(second) is None
    third = _run(env, job["id"], status="error", output="", error="boom 3")
    permalink = _record(third)
    assert permalink is not None
    facts = _facts(_note(permalink))
    assert len(facts) == 1 and "recurring pattern" in facts[0].content
    assert "3 of the last" in facts[0].content


def test_run_output_is_sanitized(env):
    job = _job(env)
    run_id = _run(env, job["id"],
                  output="Done. Mail alice@example.com with token=sk-abcdef123456 ok.")
    permalink = _record(run_id)
    note = _note(permalink)
    blob = note.content + " ".join(o.content for o in note.observations)
    assert "alice@example.com" not in blob
    assert "sk-abcdef123456" not in blob


@pytest.mark.parametrize("token", ["ghp_" + "a" * 36, "github_pat_" + "b" * 40])
def test_github_tokens_are_not_saved_in_reflexion(env, token):
    job = _job(env)
    run_id = _run(env, job["id"], output=f"Deployed using {token} successfully.")
    note = _note(_record(run_id))
    assert token not in note.content
    assert token not in " ".join(o.content for o in note.observations)


def test_long_output_stays_bounded(env):
    job = _job(env)
    run_id = _run(env, job["id"], output=("x" * 300_000))
    permalink = _record(run_id)
    note = _note(permalink)
    assert len(note.content.encode("utf-8")) <= 200_000


def test_long_job_name_does_not_block_insight(env):
    job = _job(env, name="Long job name " * 20)
    run_id = _run(env, job["id"])
    permalink = _record(run_id)
    assert permalink is not None
    assert f"run {run_id}" in _facts(_note(permalink))[0].content


def test_links_point_to_existing_notes_only(env):
    from myclaw.knowledge import write_note

    job = _job(env)
    run_id = _run(env, job["id"])
    assert _record(run_id) is not None
    assert _note(f"job-insight-{job['id']}").relations == []  # nothing to link to yet

    write_note("medic-lessons", title="Medic lessons", content="Lessons.")
    write_note("daily-report", title="Daily report", content="A note about the job.")
    second_run = _run(env, job["id"], output="Again.")
    _record(second_run)
    note = _note(f"job-insight-{job['id']}")
    links = {(r.relation_type, r.target) for r in note.relations}
    assert ("related", "medic-lessons") in links
    assert ("documents", "daily-report") in links


def test_optional_model_polish_and_fallback(env, monkeypatch):
    import myclaw.provider as provider

    job = _job(env)
    run_id = _run(env, job["id"], output="Deterministic line.")

    async def boom(cfg, messages, *a, **k):
        raise RuntimeError("no model")

    monkeypatch.setattr(provider, "complete_with_fallback", boom)
    permalink = _record(run_id, use_model=True)  # falls back to the deterministic lesson
    assert permalink is not None
    assert "Deterministic line." in _note(permalink).content

    async def fake(cfg, messages, *a, **k):
        return ('{"lesson": "A crisp model lesson."}', "p", "m")

    monkeypatch.setattr(provider, "complete_with_fallback", fake)
    second_run = _run(env, job["id"], output="Second line.")
    _record(second_run, use_model=True)
    note = _note(permalink)
    assert "A crisp model lesson." in note.content
    assert str(note.metadata["reflected_run"]) == str(second_run)


def test_user_base_isolation(env):
    job = _job(env)
    run_id = _run(env, job["id"])
    permalink = _record(run_id, user_id="alice")
    assert permalink is not None
    assert _note(permalink, "alice") is not None
    assert _note(permalink, "default") is None


# ── runner integration ───────────────────────────────────────────────────────
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


@pytest.fixture
def runner_env(env, monkeypatch):
    from myclaw.jobs import medic
    from myclaw.jobs.runner import JobRunner

    agents = {"default": _Agent("default")}
    runner = JobRunner(lambda: agents, lambda: None)

    async def no_review(run_id, **kw):
        return None

    monkeypatch.setattr(medic, "review_run", no_review)
    yield env, agents, runner


def test_runner_updates_the_note_after_a_successful_run(runner_env):
    store, agents, runner = runner_env
    job = _job(store)

    async def main():
        run_id = await runner.run_job(job["id"])
        note = None
        for _ in range(200):  # the note update is a deferred background task
            note = _note(f"job-insight-{job['id']}")
            if note is not None:
                break
            await asyncio.sleep(0.05)
        return run_id, note

    run_id, note = asyncio.run(main())
    assert note is not None
    assert str(note.metadata["reflected_run"]) == str(run_id)
    assert len(_facts(note)) == 1


def test_runner_skips_jobs_without_the_opt_in(runner_env):
    store, agents, runner = runner_env
    job = _job(store, insight_notes=False)

    async def main():
        await runner.run_job(job["id"])
        await asyncio.sleep(0.2)

    asyncio.run(main())
    assert _note(f"job-insight-{job['id']}") is None
    assert runner._reflexion_tasks == set()


def test_runner_skips_medic_created_jobs(runner_env):
    store, agents, runner = runner_env
    job = _job(store, created_by="medic")

    async def main():
        await runner.run_job(job["id"])
        await asyncio.sleep(0.2)

    asyncio.run(main())
    assert _note(f"job-insight-{job['id']}") is None


def test_stop_cancels_a_pending_reflexion(runner_env, monkeypatch):
    store, agents, runner = runner_env
    from myclaw.jobs import reflexion

    started = asyncio.Event()
    cancelled = []

    async def block(run_id, **kw):
        started.set()
        try:
            await asyncio.Event().wait()  # never finishes
        except asyncio.CancelledError:
            cancelled.append(run_id)
            raise

    monkeypatch.setattr(reflexion, "maybe_record", block)
    job = _job(store)

    async def main():
        await runner.run_job(job["id"])
        await asyncio.wait_for(started.wait(), 5)
        await runner.stop()
        return list(runner._reflexion_tasks)

    leftover = asyncio.run(main())
    assert cancelled and all(t.done() for t in leftover)
    assert _note(f"job-insight-{job['id']}") is None


def test_concurrent_records_lose_no_lesson_and_never_regress(env, tmp_path):
    """Two reflexion tasks for the same job run one after the other: the note
    ends reflecting the newest run, with no lost or duplicated write."""
    job = _job(env)
    first = _run(env, job["id"], output="First answer.")
    second = _run(env, job["id"], output="Second answer.")

    async def main():
        from myclaw.jobs import reflexion

        return await asyncio.gather(reflexion.maybe_record(first),
                                    reflexion.maybe_record(second))

    results = asyncio.run(main())
    note = _note(f"job-insight-{job['id']}")
    assert all(r == note.permalink for r in results)
    assert str(note.metadata["reflected_run"]) == str(second)  # never regresses
    contents = " ".join(o.content for o in note.observations)
    assert f"run {first}" in contents and f"run {second}" in contents
    assert len(list(_kdir(tmp_path).glob("*.md"))) == 1


def test_older_run_recorded_after_newer_is_not_lost(env):
    job = _job(env)
    first = _run(env, job["id"], output="First answer.")
    second = _run(env, job["id"], output="Second answer.")
    permalink = _record(second)
    assert _record(first) == permalink
    assert _record(first) == permalink
    note = _note(permalink)
    assert str(note.metadata["reflected_run"]) == str(second)
    contents = [o.content for o in _facts(note)]
    assert len(contents) == 2
    assert any(f"run {first}" in content for content in contents)
    assert any(f"run {second}" in content for content in contents)


def test_user_facts_added_to_the_note_are_kept(env):
    """The note is managed, but facts the user adds (other categories) survive
    the next automatic update."""
    from myclaw.knowledge import Observation, write_note

    job = _job(env)
    first = _run(env, job["id"], output="First answer.")
    permalink = _record(first)

    existing = _note(permalink)
    write_note(name=permalink, title=existing.title, content=existing.content,
               observations=[*existing.observations,
                             Observation(category="note", content="User's own pinned fact.")],
               tags=existing.tags, metadata=dict(existing.metadata))

    second = _run(env, job["id"], output="Second answer.")
    assert _record(second) == permalink
    note = _note(permalink)
    contents = [o.content for o in note.observations]
    assert any("User's own pinned fact." in c for c in contents)
    assert any(f"run {second}" in c for c in contents)
    assert str(note.metadata["reflected_run"]) == str(second)


def test_user_edits_and_many_facts_survive_updates(env):
    from myclaw.knowledge import Observation, Relation, write_note

    job = _job(env)
    first = _run(env, job["id"], output="First answer.")
    permalink = _record(first)
    existing = _note(permalink)
    user_facts = [Observation(category="note", content=f"User fact {i}") for i in range(25)]
    write_note(name=permalink, title="My edited title",
               content=existing.content + "\n\nUser-added paragraph.",
               observations=user_facts, relations=[Relation("related", "my-note", strength=3)],
               tags=["my-tag"], aliases=["my-alias"],
               metadata={**existing.metadata, "my-key": "my-value"})
    second = _run(env, job["id"], output="Second answer.")
    _record(second)
    note = _note(permalink)
    assert note.title == "My edited title"
    assert "User-added paragraph." in note.content
    assert {f"User fact {i}" for i in range(25)} <= {o.content for o in note.observations}
    assert "my-tag" in note.tags and "my-alias" in note.aliases
    assert note.metadata["my-key"] == "my-value"
    assert any(r.target == "my-note" and r.strength == 3 for r in note.relations)
