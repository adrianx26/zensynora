"""ZenSynora doctor — real, modular health checks (used by the medic agent,
``zensynora doctor`` and the Web UI "Doctor" page).

Each check is a small function registered with ``@check``; it returns a
``CheckResult`` (ok / warn / fail / skip) with a plain-language detail and a
hint on how to fix it. Checks that can repair themselves safely register a
``fix`` function. Adding a check = adding one decorated function in
``checks.py``; nothing else needs to change.
"""

from .registry import CheckResult, Report, apply_fixes, check, registered_checks, run_checks

__all__ = ["CheckResult", "Report", "check", "registered_checks", "run_checks", "apply_fixes"]
