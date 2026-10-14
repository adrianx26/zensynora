"""The medic looks at a failed job run, says why it failed and how to fix it (v0.7.5).

* :func:`diagnose` — cause, plain-language explanation, suggested job changes,
  things only you can do (load a model, add a key) and whether ZenSynora's code
  needs fixing. Known failure patterns are recognised by rules first, so there
  is a useful answer even when no model can be reached; a model then refines it.
* :func:`apply_fix` — applies the suggested job changes (only safe job fields:
  prompt, agent, timeout, retries, tool steps, criteria, fresh context) and
  records a lesson in the knowledge base.
* :func:`code_fix_job` — when the code itself is at fault, a medic job with
  source-reading tools files a change proposal. Nothing is installed until you
  approve it on the Doctor page.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from . import store

logger = logging.getLogger(__name__)

FIXABLE = ("prompt", "agent", "timeout_s", "retries", "max_steps", "success_criteria", "fresh_context")
CATEGORIES = ("model", "timeout", "tool_steps", "prompt", "tools", "config", "code", "external", "unknown")

MEDIC_PROMPT = """You are ZenSynora's medic. A job (an instruction an agent runs on a schedule)
failed. Find the most likely cause and the smallest fix.

Reply with ONLY a JSON object:
{"cause": "one short sentence",
 "category": "model|timeout|tool_steps|prompt|tools|config|code|external|unknown",
 "explanation": "2-4 plain sentences for a non-expert",
 "job_changes": {"prompt": "...", "agent": "...", "timeout_s": 600, "retries": 1, "max_steps": 12,
                 "success_criteria": "...", "fresh_context": true},
 "manual_steps": ["what only the user can do, e.g. load the model in LM Studio"],
 "code_fix_needed": false,
 "confidence": 0.7}

Rules:
- job_changes: include ONLY fields that help with THIS failure; {} if none. "agent" must be
  one of the listed agents (switching agent is how to use another model).
- max_steps (1-30) is how many extra tool rounds the agent gets per run; raise it when the
  run ended with "needed more tool steps". A clearer, narrower prompt also helps.
- A server that cannot be reached, a model that is not loaded, a missing API key or credits
  are manual_steps (or switch to an agent whose model works).
- code_fix_needed only when ZenSynora itself behaves wrongly (a crash or traceback in its
  code), not when a model or website fails.
- Keep the user's intent when rewriting the prompt."""


# ── rules for failures we know ──────────────────────────────────────────────
def rule_diagnosis(job: dict[str, Any], run: dict[str, Any]) -> dict[str, Any] | None:
    """Fast diagnosis for common failures (works without any model)."""
    text = f"{run.get('error') or ''}\n{run.get('output') or ''}"
    low = text.lower()
    steps = int(job.get("max_steps") or 8)
    if "needed more tool steps" in low:
        return {
            "cause": "The agent needed more tool steps than the job allows.",
            "category": "tool_steps",
            "explanation": (f"The agent was still working (searching, reading pages, retrying) when it used up "
                            f"its {steps} tool steps for this run. Allowing more steps usually fixes it; a "
                            "more specific prompt (for example naming the website to use) makes it faster."),
            "job_changes": {"max_steps": min(30, max(steps * 2, 12))},
            "manual_steps": [], "code_fix_needed": False, "confidence": 0.8,
        }
    if run.get("status") == "timeout" or "took too long" in low:
        t = int(job.get("timeout_s") or 300)
        return {
            "cause": "The run did not finish within the time limit.",
            "category": "timeout",
            "explanation": (f"The job stops after {t} s. Slow local models and multi-step tasks often need "
                            "longer. If the model server is overloaded, a faster model helps more."),
            "job_changes": {"timeout_s": min(3600, max(t * 2, 600))},
            "manual_steps": [], "code_fix_needed": False, "confidence": 0.7,
        }
    if any(s in low for s in ("cannot be reached", "connection refused", "connecterror", "request timed out",
                               "failed to connect", "no model loaded", "model not found", "is not loaded")):
        return {
            "cause": "The agent's model server could not be reached or has no model loaded.",
            "category": "model",
            "explanation": ("The agent could not talk to its model. A local server (LM Studio, Ollama, "
                            "llama.cpp) may be stopped, on another address, or without the model loaded."),
            "job_changes": {},
            "manual_steps": ["Start the model server and load the model, or pick an agent whose model works "
                             "(Models page → Test)."],
            "code_fix_needed": False, "confidence": 0.75,
        }
    if any(s in low for s in ("rate-limited", "rate limit", "429")):
        return {
            "cause": "The model is rate-limited (too many requests for a free model).",
            "category": "model",
            "explanation": "Free models are often busy. Retrying later, or a second model in rotation, helps.",
            "job_changes": {"retries": max(1, int(job.get("retries") or 0))},
            "manual_steps": ["Add a second model to the rotation list on the Models page."],
            "code_fix_needed": False, "confidence": 0.7,
        }
    if any(s in low for s in ("only available", "403", "credits", "insufficient", "401", "api key")):
        return {
            "cause": "The model provider refused the request (access, key or credits).",
            "category": "config",
            "explanation": "The provider did not allow this model to answer — check the key, credits, or "
                           "use another model.",
            "job_changes": {},
            "manual_steps": ["Use Test on the Models page and switch the agent to a model that works."],
            "code_fix_needed": False, "confidence": 0.7,
        }
    if "traceback" in low or re.search(r"\b(TypeError|AttributeError|KeyError|NameError)\b", text):
        return {
            "cause": "ZenSynora's code raised an error while running the job.",
            "category": "code",
            "explanation": "This looks like a bug in ZenSynora, not in the model. The medic can look at the "
                           "code and propose a fix for you to approve.",
            "job_changes": {}, "manual_steps": [], "code_fix_needed": True, "confidence": 0.6,
        }
    return None


def _clean(d: dict[str, Any], agents: list[str], job: dict[str, Any]) -> dict[str, Any]:
    """Keep only valid fields and values the job can take."""
    changes = {}
    for k, v in (d.get("job_changes") or {}).items():
        if k not in FIXABLE or v in (None, ""):
            continue
        try:
            if k in ("timeout_s", "retries", "max_steps"):
                v = int(v)
                v = {"timeout_s": min(max(v, 10), 7200), "retries": min(max(v, 0), 5),
                     "max_steps": min(max(v, 1), 30)}[k]
            elif k == "fresh_context":
                v = bool(v)
            elif k == "agent":
                v = next((a for a in agents if a.lower() == str(v).lower()), None)
                if v is None:
                    continue
            else:
                v = str(v).strip()
        except (TypeError, ValueError):
            continue
        if v != job.get(k):
            changes[k] = v
    cat = d.get("category") if d.get("category") in CATEGORIES else "unknown"
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    return {
        "cause": str(d.get("cause") or "Unknown cause")[:300],
        "category": cat,
        "explanation": str(d.get("explanation") or "")[:1500],
        "job_changes": changes,
        "manual_steps": [str(s)[:300] for s in (d.get("manual_steps") or [])][:5],
        "code_fix_needed": bool(d.get("code_fix_needed")),
        "confidence": conf,
    }


def _parse(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise ValueError("the medic did not answer in JSON")
    return json.loads(m.group(0))


def _context(job: dict[str, Any], run: dict[str, Any], agents: dict[str, str]) -> str:
    history = [r for r in store.list_runs(job["id"], limit=8) if r["id"] != run["id"]]
    lines = [
        f"JOB: {job['name']}",
        f"Prompt: {job['prompt']}",
        f"Agent: {job['agent']} (model {agents.get(job['agent'], '?')})",
        f"Timeout: {job.get('timeout_s')} s · retries: {job.get('retries')} · tool steps: {job.get('max_steps')}"
        f" · fresh context: {job.get('fresh_context')}",
        f"Success criteria: {job.get('success_criteria') or '(none)'}",
        "",
        f"FAILED RUN #{run['id']}: status {run['status']}, model {run.get('provider')}/{run.get('model')}, "
        f"{(run.get('ms') or 0) / 1000:.0f} s, attempt {run.get('attempt')}",
        f"Error: {(run.get('error') or '')[:1500]}",
        f"Output (end): {(run.get('output') or '')[-2500:]}",
        "",
        "EARLIER RUNS: " + ("; ".join(f"#{r['id']} {r['status']}"
                                       + (f" ({(r.get('error') or '')[:80]})" if r['status'] != 'ok' else '')
                                       for r in history) or "none"),
        "",
        "AGENTS: " + ", ".join(f"{n} ({m})" for n, m in agents.items()),
    ]
    try:
        from ..selfupdate import recent_problems

        errs = recent_problems(hours=6, limit=8).get("errors", [])
        if errs:
            lines += ["", "RECENT ERRORS IN THE LOG:"] + [f"- ({e['count']}x) {e['message'][:250]}" for e in errs]
    except Exception as e:  # log unreadable — not needed for a diagnosis
        logger.debug("medic: no log context: %s", e)
    return "\n".join(lines)


def _agents(config: Any) -> dict[str, str]:
    d = config.agents.defaults
    out = {"default": f"{d.provider}/{d.model}"}
    for a in config.agents.named:
        out[a.name] = f"{a.provider or d.provider}/{a.model or d.model}"
    return out


async def diagnose(run_id: int, use_model: bool = True) -> dict[str, Any]:
    """Diagnose a failed run; the result is stored with the run."""
    from ..config import load_config

    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise KeyError(run_id)
    job = await asyncio.to_thread(store.get_job, run["job_id"])
    if job is None:
        raise KeyError(run["job_id"])
    config = load_config()
    agents = _agents(config)
    rule = rule_diagnosis(job, run)
    result: dict[str, Any] | None = None
    note = ""
    if use_model:
        from ..provider import complete_with_fallback

        content = await asyncio.to_thread(_context, job, run, agents)
        if rule:
            content += f"\n\nFIRST GUESS (rule-based, may be incomplete): {rule['cause']}"
        try:
            text, prov, model = await complete_with_fallback(
                config, [{"role": "system", "content": MEDIC_PROMPT}, {"role": "user", "content": content}])
            result = _clean(_parse(text), list(agents), job)
            result["by"] = f"{prov}/{model}"
        except Exception as e:
            note = f"No model could do a full diagnosis ({str(e)[:200]})."
            logger.info("medic diagnosis of run %s fell back to rules: %s", run_id, e)
    if result is None:
        base = rule or {"cause": "The cause is not clear from the error.", "category": "unknown",
                        "explanation": "No known pattern matched and no model was available for a deeper look. "
                                       "Check the run's output and the Doctor page.",
                        "job_changes": {}, "manual_steps": [], "code_fix_needed": False, "confidence": 0.2}
        result = _clean(base, list(agents), job)
        result["by"] = "rules"
    elif rule and not result["job_changes"] and rule.get("job_changes"):
        # The model explained it but forgot the obvious change the rules know.
        result["job_changes"] = _clean(rule, list(agents), job)["job_changes"]
    if note:
        result["note"] = note
    result.update(run_id=run_id, job_id=job["id"], at=time.time(),
                  current={k: job.get(k) for k in result["job_changes"]})
    await asyncio.to_thread(store.set_diagnosis, run_id, result)
    return result


async def apply_fix(run_id: int) -> dict[str, Any]:
    """Apply the diagnosis' job changes; returns the updated job."""
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise KeyError(run_id)
    diag = run.get("diagnosis") or {}
    changes = diag.get("job_changes") or {}
    if not changes:
        raise ValueError("The medic suggested no change to the job for this failure.")
    job = await asyncio.to_thread(store.update_job, run["job_id"], dict(changes))
    if job is None:
        raise KeyError(run["job_id"])
    diag.update(applied=True, applied_at=time.time())
    await asyncio.to_thread(store.set_diagnosis, run_id, diag)
    try:
        from ..selfupdate import record_lesson

        await asyncio.to_thread(
            record_lesson, f"job '{job['name']}'",
            f"Failed: {diag.get('cause')} Fixed by changing {', '.join(changes)}.")
    except Exception as e:  # knowledge base unavailable — the fix still counts
        logger.debug("could not record lesson: %s", e)
    return job


CODE_FIX_PROMPT = """A job run failed and the cause looks like a bug in ZenSynora's own code.

Job: {job}
Error: {error}
Diagnosis: {cause} — {explanation}

1. Find the code involved: list_source_files and read_source_file (start with the module
   named in the error or traceback, if any).
2. If you find the bug, call propose_code_change with the smallest correct fix and a clear
   reason. The user reviews and approves it on the Doctor page — never claim it is installed.
3. If you cannot find it, explain what you checked.
4. Record what you learned with record_lesson."""


async def code_fix_job(run_id: int) -> dict[str, Any]:
    """Create (or reuse) a medic job that proposes a code fix for this failure."""
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise KeyError(run_id)
    job = await asyncio.to_thread(store.get_job, run["job_id"])
    diag = run.get("diagnosis") or {}
    prompt = CODE_FIX_PROMPT.format(job=job["name"] if job else run["job_id"],
                                    error=(run.get("error") or run.get("output") or "")[:2000],
                                    cause=diag.get("cause", "?"), explanation=diag.get("explanation", ""))
    name = f"Medic code fix: {job['name'] if job else run['job_id']}"[:100]
    existing = next((j for j in await asyncio.to_thread(store.list_jobs) if j["name"] == name), None)
    data = {"name": name, "prompt": prompt, "agent": "default", "schedule": "manual", "timeout_s": 900,
            "max_steps": 16, "created_by": "medic",
            "success_criteria": "Names the faulty code and files a change proposal, or explains why not."}
    if existing:
        return await asyncio.to_thread(store.update_job, existing["id"], data)
    return await asyncio.to_thread(store.create_job, data)
