"""Doctor checks of the knowledge base (KB phase 6, 2026-10-25).

Index drift, waiting links, open conflicts, embedding coverage, distillation,
the file watcher, import errors and the size of ``.trash`` — for every
knowledge base, read-only and fast (``myclaw/knowledge/health.py``, the same
rules as ``GET /api/knowledge/health``). The only automatic fix is the safe
one: re-index the bases whose index drifted (``zensynora doctor --fix``).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from .registry import OK, WARN, check, fixer

CAT = "knowledge"


def _kb_on(ctx) -> bool:
    try:
        return bool(ctx.get_config().knowledge.enabled)
    except BaseException:  # a broken config is reported by its own check
        return True


def _cached(ctx, key: str, fn: Callable[[], Any]) -> Any:
    k = f"kb:{key}"
    if k not in ctx.cache:
        ctx.cache[k] = fn()
    return ctx.cache[k]


def _bases(ctx) -> List[str]:
    from ..knowledge import health

    return _cached(ctx, "bases", health.bases)


def _per_base(ctx, part: str) -> Dict[str, Dict[str, Any]]:
    from ..knowledge import health

    fns = {"index": health.index_drift, "links": health.links, "conflicts": health.conflicts,
           "embeddings": health.embeddings, "distill": health.distillation, "trash": health.trash}
    def safe(base: str) -> Dict[str, Any]:
        try:
            return fns[part](base)
        except Exception as e:  # a check never crashes on a broken base
            return {"error": f"{type(e).__name__}: {e}"}

    return {b: _cached(ctx, f"{part}:{b}", lambda b=b: safe(b)) for b in _bases(ctx)}


def _warnings(ctx, part: str) -> List[str]:
    """Warning texts + fixes of one part, per base (the health route's rules)."""
    from ..knowledge import health

    out: List[str] = []
    for base, data in _per_base(ctx, part).items():
        if data.get("error") and part != "index":
            out.append((f"{base}: " if base != "default" else "") + f"could not be checked: {data['error']}"
                       + "\0" + "Run zensynora knowledge sync --force (rebuilds the index from the notes).")
            continue
        for w in health.warnings_for({part: data}):
            out.append((f"{base}: " if base != "default" else "") + w["text"] + "\0" + w["fix"])
    return out


def _result(ws: List[str], ok_detail: str):
    if not ws:
        return OK, ok_detail
    texts = [w.split("\0")[0] for w in ws]
    fixes = list(dict.fromkeys(w.split("\0")[1] for w in ws))
    return WARN, "; ".join(texts), " ".join(fixes)


# ── index drift ─────────────────────────────────────────────────────────────

def _drifted(ctx) -> List[str]:
    from ..knowledge import health

    out = []
    for base, d in _per_base(ctx, "index").items():
        total = health.drift_total(d)
        if d.get("error") or (d.get("fts") is False and d.get("indexed")) or \
                total >= 3 or (total and total * 10 > max(1, d.get("files", 0))):
            out.append(base)
    return out


@check("knowledge_index", "Knowledge index", "data")
def _knowledge_index(ctx):
    from ..knowledge import health

    if not _kb_on(ctx):
        return OK, "knowledge base turned off"
    per = _per_base(ctx, "index")
    if not per or not any(d.get("files") or d.get("db") for d in per.values()):
        return OK, "no notes yet"
    bad = _drifted(ctx)
    parts = []
    for base, d in per.items():
        label = "" if base == "default" and len(per) == 1 else f"{base}: "
        text = health.drift_text(d)
        if d.get("fts") is False and d.get("indexed"):
            text += "; full-text index of note text missing"
        if d.get("error"):
            text += f"; {d['error']}"
        if base in bad or len(per) <= 3:
            parts.append(label + text)
    detail = "; ".join(parts) or f"{len(per)} knowledge bases in sync"
    hint = ("Run zensynora knowledge sync (or zensynora doctor --fix); zensynora knowledge sync "
            "--force rebuilds the whole index. Turn on Settings → Features → Watch note files to "
            "re-index notes edited by hand automatically.")
    if bad:
        return WARN, detail, hint
    return OK, detail


@fixer("knowledge_index")
def _fix_knowledge_index(ctx):
    from ..knowledge.sync import background_sync

    done = []
    added = updated = deleted = 0
    bases = _drifted(ctx)
    if not bases:   # 2026-10-27 (review)
        return "nothing needed re-indexing"
    for base in bases:
        s = background_sync(base, "doctor --fix")
        added += s.get("added", 0)
        updated += s.get("updated", 0)
        deleted += max(0, s.get("deleted", 0))
        done.append(base)
        ctx.cache.pop(f"kb:index:{base}", None)
    return (f"knowledge synced ({', '.join(done)}): {added} added, {updated} updated, "
            f"{deleted} removed")


# ── the other parts ─────────────────────────────────────────────────────────

@check("kb_links", "Knowledge: waiting links", CAT)
def _kb_links(ctx):
    if not _kb_on(ctx):
        return OK, "knowledge base turned off"
    per = _per_base(ctx, "links")
    total = sum(d.get("pending", 0) for d in per.values())
    top = [t["target"] for d in per.values() for t in d.get("top", [])][:3]
    return _result(_warnings(ctx, "links"),
                   f"{total} link(s) to notes that do not exist yet" + (f" (most: {', '.join(top)})" if top else ""))


@check("kb_conflicts", "Knowledge: conflicts", CAT)
def _kb_conflicts(ctx):
    if not _kb_on(ctx):
        return OK, "knowledge base turned off"
    return _result(_warnings(ctx, "conflicts"), "no open conflicts")


@check("kb_embeddings", "Knowledge: semantic search", CAT)
def _kb_embeddings(ctx):
    from ..knowledge import semantic

    if not _kb_on(ctx):
        return OK, "knowledge base turned off"
    if not semantic.enabled():
        return OK, "semantic search is off (knowledge.semantic_search)"
    per = _per_base(ctx, "embeddings")
    notes = sum(d.get("notes", 0) for d in per.values())
    done = sum(d.get("embedded", 0) for d in per.values())
    return _result(_warnings(ctx, "embeddings"), f"{done} of {notes} note(s) embedded ({semantic.model_name()})")


@check("kb_distill", "Knowledge: distillation", CAT)
def _kb_distill(ctx):
    from ..knowledge import distill

    if not _kb_on(ctx):
        return OK, "knowledge base turned off"
    if not distill.enabled():
        return OK, "distillation is off (knowledge.distill)"
    per = _per_base(ctx, "distill")
    d = per.get("default") or next(iter(per.values()), {})
    lease = (d.get("lease") or {}).get("state")
    detail = (f"last run {d.get('last_run') or 'never'}, {sum(x.get('pending') or 0 for x in per.values())} "
              f"note(s) pending" + (f", lease {lease}" if lease else ""))
    return _result(_warnings(ctx, "distill"), detail)


@check("kb_watcher", "Knowledge: file watcher", CAT)
def _kb_watcher(ctx):
    from ..knowledge import health

    if not _kb_on(ctx):
        return OK, "knowledge base turned off"
    wt = _cached(ctx, "watcher", health.watcher)
    if not wt.get("enabled"):
        return OK, "off (Settings → Features → Watch note files) — re-index after editing notes by hand"
    ws = [w["text"] + "\0" + w["fix"] for w in health.warnings_for({"watcher": wt})]
    if not wt.get("wanted"):
        detail = "on — runs in the Web UI"
    else:
        mode = wt.get("mode") or "?"
        detail = f"running ({mode})" if wt.get("running") else "not running"
        if wt.get("last_reindex"):
            import time

            detail += f", last re-index {time.strftime('%H:%M:%S', time.localtime(wt['last_reindex']))}"
    if not wt.get("watchfiles_installed"):
        detail += f"; polling every {wt.get('poll_seconds', 5):.0f} s (instant updates: {wt.get('install_hint')})"
    return _result(ws, detail)


@check("kb_ingest", "Knowledge: imports", CAT)
def _kb_ingest(ctx):
    from ..knowledge import health

    if not _kb_on(ctx):
        return OK, "knowledge base turned off"
    ig = _cached(ctx, "ingest", health.ingest_errors)
    ws = [w["text"] + "\0" + w["fix"] for w in health.warnings_for({"ingest": ig})]
    return _result(ws, f"{ig.get('runs', 0)} import(s) in the last {health.INGEST_HOURS} h, none failed")


@check("kb_trash", "Knowledge: .trash", CAT)
def _kb_trash(ctx):
    from ..knowledge import health

    if not _kb_on(ctx):
        return OK, "knowledge base turned off"
    per = _per_base(ctx, "trash")
    files = sum(d.get("files", 0) for d in per.values())
    size = sum(d.get("bytes", 0) for d in per.values())
    return _result(_warnings(ctx, "trash"),
                   f"{files} earlier version(s) / deleted note(s), {health.human_bytes(size)}")
