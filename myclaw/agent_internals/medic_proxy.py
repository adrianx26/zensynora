"""Tiny indirection over ``myclaw.agents.medic_agent.prevent_infinite_loop``.

Lives in its own module so the router can import it cleanly and tests
can monkey-patch it without touching ``myclaw.agents.medic_agent``
(which has expensive side effects on import).
"""

from __future__ import annotations


def medic_loop_check() -> bool:
    """Return True if the medic agent says we should stop processing.

    A False return — including the import-failure path — lets the request
    proceed. This is intentional: medic is optional infrastructure.
    """
    try:
        from ..agents.medic_agent import prevent_infinite_loop
    except Exception:
        return False
    try:
        status = prevent_infinite_loop()
        return "limit reached" in (status or "").lower()
    except Exception:
        return False
