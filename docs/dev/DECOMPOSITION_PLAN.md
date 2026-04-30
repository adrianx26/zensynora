# Agent.py Decomposition Plan

> **Status:** **EXECUTED** in Sprint 5 (2026-04-30). agent.py shrank from
> 1784 → 1487 lines (-16%); 14 new isolated tests; 175 pre-existing
> tests unchanged.
> The pre-existing broken stub package ``myclaw/agent/`` (which silently
> shadowed agent.py for eight import sites) was also deleted as part of
> this sprint.

## Outcome

- **`myclaw/agent_internals/router.py`** — `route_message(agent, ...)`
- **`myclaw/agent_internals/context_builder.py`** — `build_message_context(agent, ...)`
- **`myclaw/agent_internals/tool_executor.py`** — `execute_tools(agent, ...)`
- **`myclaw/agent_internals/medic_proxy.py`** — testable indirection
- ``Agent._route_message`` / ``_build_context`` / ``_execute_tools``
  collapsed to ~6-line delegating wrappers each.

## What still remains

> **Phase 2 status (Sprint 9):** items 2 and 3 below are now done. Item 1
> remains as the only open axis.

1. Replace the implicit `agent` parameter with explicit dependency injection
   (timer, memory provider, hooks…) so each helper can be unit-tested
   without a stub Agent at all. **Open.**
2. ✅ Convert the helpers to classes (`MessageRouter`, `ContextBuilder`,
   `ToolExecutor`) with a tiny `Agent` orchestrator that composes them.
   Done in Sprint 9 — see `myclaw/agent_internals/classes.py`. The
   classes currently *wrap* the free functions to preserve behavior;
   future work can inline the bodies once the surrounding code paths
   are stable.
3. ✅ Extract `ResponseHandler` (post-LLM formatting + summarization
   triggers) which Sprint 5 left untouched. Done in Sprint 9 — proper
   class with explicit dependency surface; ``Agent._handle_summarization``
   is now a 4-line wrapper.

These changes landed independently; the public `Agent` API has not
moved. See the original phasing below for context.

---

## Original phasing (now historical)

## Why we keep deferring it

The `Agent` class in `myclaw/agent.py` is ~1700 lines and 22 methods. A
clean split into `ContextBuilder`, `ToolExecutor`, `ResponseHandler`, and
`MessageRouter` is **the right thing**, but every sprint we've weighed
the cost (4–6 days, large diff, real regression risk in the request hot
path) against shipping new user-visible features and chosen the latter.

The four Sprint 1–4 deferrals were each correct given the alternatives.
That doesn't mean it should be deferred indefinitely — every additional
feature added to the monolith makes the eventual split harder.

## What "done" looks like

* `Agent` becomes a thin orchestrator. ~200 lines, mostly composition
  and state.
* `ContextBuilder.build(user_message, history, user_id, mem) -> messages`
  owns memory retrieval, knowledge search, system-prompt assembly,
  optional summarization triggers.
* `ToolExecutor.execute(tool_calls, ...) -> ToolExecutionResult` owns
  parallel/sequential dispatch, audit logging, KB extraction triggers.
* `ResponseHandler.format(messages, response) -> str` owns post-LLM
  formatting, KB auto-save, summarization side-effects.
* `MessageRouter.route(user_message, user_id, depth) -> RouteResult`
  owns the timer, guardrails, depth checks, model selection.

## Suggested phasing (3 PRs)

### PR 1 — `MessageRouter` (lowest risk)
* Self-contained: takes a config + memory and returns a route decision.
* No tool execution, no LLM call, no streaming.
* Can be tested with mocks easily.
* **~600 lines moved, 1 day.**

### PR 2 — `ContextBuilder`
* Touches memory + knowledge — has more collaborators than the router.
* Needs careful preservation of the existing knowledge-gap caching and
  the `had_kb_results` flag that downstream code uses.
* **~500 lines moved, 2 days.**

### PR 3 — `ToolExecutor` + `ResponseHandler`
* Tool execution is the hottest path; do it last when you have the
  most confidence in the surrounding seams.
* **~600 lines moved, 2–3 days.**

## Acceptance criteria for any decomposition PR

1. `tests/test_agent.py` passes unchanged.
2. The full Sprint 1–4 test suite (~135 tests) passes unchanged.
3. No public method on `Agent` changes signature without a deprecation
   shim retained for at least one minor version.
4. `Agent.think()` end-to-end latency on the smoke benchmark within ±5%
   of pre-decomposition baseline.
5. Each new module has its own dedicated test file.

## Pre-flight checklist

Before opening PR 1:
* [ ] Get a baseline timing of `Agent.think` on the existing smoke test.
* [ ] Add fine-grained logging at each future seam in the current
  `agent.py` so observed traces match expectations after the split.
* [ ] Lock the public surface of `Agent` (add `__all__`, mark internals
  with `_`) — much of the current "public" surface is accidental.
