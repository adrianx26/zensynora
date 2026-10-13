"""Check registry and runner for the doctor."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
_ORDER = {FAIL: 0, WARN: 1, OK: 2, SKIP: 3}


@dataclass
class CheckResult:
    id: str
    title: str
    category: str
    status: str
    detail: str = ""
    hint: str = ""
    fixable: bool = False
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Check:
    id: str
    title: str
    category: str
    func: Callable[[DoctorContext], CheckResult]
    fix: Callable[[DoctorContext], str] | None = None
    network: bool = False


@dataclass
class DoctorContext:
    offline: bool = False
    config: object = None
    cache: dict[str, object] = field(default_factory=dict)

    def get_config(self):
        if self.config is None:
            from ..config import load_config

            self.config = load_config()
        return self.config


_CHECKS: dict[str, _Check] = {}


def check(id: str, title: str, category: str, network: bool = False):
    """Register a check. The function receives a DoctorContext and returns
    ``(status, detail, hint)`` or a full ``CheckResult``."""

    def deco(func):
        _CHECKS[id] = _Check(id=id, title=title, category=category, func=func, network=network)
        return func

    return deco


def fixer(id: str):
    """Register an automatic, safe fix for check ``id``."""

    def deco(func):
        _CHECKS[id].fix = func
        return func

    return deco


def registered_checks() -> list[_Check]:
    from . import checks  # noqa: F401  (registers the built-in checks)

    return list(_CHECKS.values())


@dataclass
class Report:
    results: list[CheckResult]
    generated_at: str
    version: str

    @property
    def summary(self) -> dict[str, int]:
        out = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    @property
    def overall(self) -> str:
        s = self.summary
        return FAIL if s[FAIL] else WARN if s[WARN] else OK

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "summary": self.summary,
            "version": self.version,
            "generated_at": self.generated_at,
            "results": [r.to_dict() for r in self.results],
        }

    def to_text(self, show_ok: bool = True) -> str:
        icons = {OK: "✔", WARN: "!", FAIL: "✘", SKIP: "-"}
        lines = [f"ZenSynora {self.version} — health report ({self.generated_at})"]
        current = None
        for r in sorted(self.results, key=lambda r: (r.category, _ORDER[r.status])):
            if r.status == OK and not show_ok:
                continue
            if r.category != current:
                current = r.category
                lines.append(f"\n[{current}]")
            lines.append(f"  {icons[r.status]} {r.title}: {r.detail}")
            if r.hint and r.status in (WARN, FAIL):
                lines.append(f"      → {r.hint}{'  (auto-fixable: zensynora doctor --fix)' if r.fixable else ''}")
        s = self.summary
        lines.append(f"\nOverall: {self.overall.upper()} — {s[OK]} ok, {s[WARN]} warnings, {s[FAIL]} failures, {s[SKIP]} skipped")
        return "\n".join(lines)


def run_checks(
    categories: Iterable[str] | None = None,
    offline: bool = False,
    only: Iterable[str] | None = None,
    ctx: DoctorContext | None = None,
) -> Report:
    from .. import __version__

    ctx = ctx or DoctorContext(offline=offline)
    cats = set(categories) if categories else None
    ids = set(only) if only else None
    results: list[CheckResult] = []
    for c in registered_checks():
        if cats and c.category not in cats:
            continue
        if ids and c.id not in ids:
            continue
        start = time.monotonic()
        if c.network and ctx.offline:
            res = CheckResult(c.id, c.title, c.category, SKIP, "skipped (offline mode)")
        else:
            try:
                out = c.func(ctx)
                if isinstance(out, CheckResult):
                    res = out
                else:
                    status, detail, hint = (list(out) + ["", ""])[:3]
                    res = CheckResult(c.id, c.title, c.category, status, detail, hint)
            except Exception as e:  # a broken check must never break the report
                logger.debug("doctor check %s crashed", c.id, exc_info=True)
                res = CheckResult(c.id, c.title, c.category, FAIL, f"check crashed: {e}",
                                  "Please report this as a bug.")
        res.fixable = bool(c.fix) and res.status in (WARN, FAIL)
        res.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(res)
    return Report(results=results, generated_at=time.strftime("%Y-%m-%d %H:%M:%S"), version=__version__)


def apply_fixes(report: Report, ctx: DoctorContext | None = None) -> list[str]:
    """Run the safe automatic fixes for every fixable warning/failure."""
    ctx = ctx or DoctorContext()
    by_id = {c.id: c for c in registered_checks()}
    done: list[str] = []
    for r in report.results:
        c = by_id.get(r.id)
        if r.fixable and c and c.fix:
            try:
                done.append(f"{r.title}: {c.fix(ctx)}")
            except Exception as e:
                done.append(f"{r.title}: fix failed — {e}")
    return done
