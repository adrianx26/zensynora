"""Shared :class:`requests.Session` used throughout the codebase.

Having a single session enables HTTP connection pooling, keep‑alive and
re‑uses TCP sockets, which reduces latency for frequent outbound calls.
"""

import requests
from threading import Lock

_session: requests.Session | None = None
_lock = Lock()


def get_session() -> requests.Session:
    """Return a lazily‑created, thread‑safe ``requests.Session``.

    The first call creates the session and subsequent calls return the same
    instance.  The function is safe to call from multiple threads.
    """
    global _session
    if _session is None:
        with _lock:
            if _session is None:
                _session = requests.Session()
                # Configure reasonable defaults (e.g., a default timeout can be
                # set per‑request; we keep the session lightweight here).
    return _session
