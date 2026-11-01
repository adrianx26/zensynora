"""2026-11-01: every router in myclaw/web is part of the real Web UI app.

The knowledge usage API (Usage tab, benchmarks) was written and tested with its
own test app, but never added to myclaw/web/api.py — the page got 404s. This
test checks every module of myclaw/web that defines a ``router``.
"""

from __future__ import annotations

import importlib
import pkgutil


def test_every_web_router_is_registered():
    import myclaw.web as web
    from myclaw.web.api import app

    # the OpenAPI schema lists every HTTP route, however FastAPI stores included routers
    served = {(path, m.upper()) for path, ops in app.openapi()["paths"].items() for m in ops}
    missing = []
    for mod in pkgutil.iter_modules(web.__path__):
        module = importlib.import_module(f"myclaw.web.{mod.name}")
        router = getattr(module, "router", None)
        if router is None or module is importlib.import_module("myclaw.web.api"):
            continue
        for r in router.routes:
            if not getattr(r, "include_in_schema", True):
                continue
            for m in getattr(r, "methods", None) or ():
                if m != "HEAD" and (r.path, m) not in served:
                    missing.append(f"{mod.name}: {m} {r.path}")
    assert not missing, "routers not added to myclaw/web/api.py:\n" + "\n".join(missing)
