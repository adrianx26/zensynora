"""Knowledge base health for the Web UI (KB phase 6, 2026-10-25).

GET /api/knowledge/health?base=   index drift, waiting links, open conflicts,
                                  embeddings, distillation, file watcher,
                                  import errors, .trash size and the warnings
                                  with what to do (the same rules as the doctor)

Read-only and fast; guarded like the other knowledge routes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter

from myclaw.web.knowledge_api import _base
from myclaw.web.settings_api import guard

router = APIRouter()


@router.get("/api/knowledge/health", dependencies=guard)
async def knowledge_health(base: Optional[str] = None) -> Dict[str, Any]:
    from myclaw.knowledge import health

    return await asyncio.to_thread(health.health, _base(base))
