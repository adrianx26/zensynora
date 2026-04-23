from pathlib import Path
from fastapi import FastAPI, WebSocket, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import asyncio
import logging
import time
import uuid

from myclaw.config import load_config
from myclaw.tools import TOOLS, set_registry, load_custom_tools
from myclaw.agent import Agent
from myclaw.metrics import setup_metrics_endpoint, MetricsMiddleware, get_metrics
from myclaw.admin_dashboard import (
    build_dashboard_data,
    register_websocket_session,
    unregister_websocket_session,
    update_session_activity,
    record_response_time,
)
from myclaw.cost_tracker import get_monthly_costs, get_cost_summary
from myclaw.knowledge_spaces import (
    create_space,
    delete_space,
    add_member,
    remove_member,
    get_space,
    list_spaces,
    check_permission,
)
from myclaw.mfa import MFAAuth
from myclaw.metering import record_call, check_quota
from myclaw.web.auth import require_admin_api_key

logger = logging.getLogger(__name__)

_mfa = MFAAuth()

# ── Agent Registry (shared across WebSocket connections) ─────────────────────
_agent_registry: dict = {}


def _ensure_registry() -> dict:
    """Build and cache the agent registry on first use."""
    global _agent_registry
    if _agent_registry:
        return _agent_registry

    config = load_config()
    registry: dict = {"default": Agent(config, name="default")}

    for nc in config.agents.named:
        registry[nc.name] = Agent(
            config,
            name=nc.name,
            model=nc.model,
            system_prompt=nc.system_prompt or None,
            provider_name=nc.provider or None,
        )

    load_custom_tools()
    set_registry(registry)
    _agent_registry = registry
    logger.info(f"Web UI agent registry built: {list(registry.keys())}")
    return registry


app = FastAPI(title="ZenSynora Web UI Backend", version="1.0.1")

# Metrics middleware (must be first to capture all requests)
app.add_middleware(MetricsMiddleware)

# Metrics endpoint
setup_metrics_endpoint(app)

# SECURITY FIX: CORS origins are now loaded from config instead of wildcard.
# Wildcard + credentials is a security breach (any website can steal sessions).
_config = load_config()
_cors_origins = getattr(_config, "security", None)
_cors_origins = _cors_origins.cors_origins if _cors_origins else ["http://localhost:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    message: str
    agent_name: str = "default"


# ── Health Check (used by Docker) ───────────────────────────────────────────
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "zensynora-webui"}


@app.get("/api/status")
async def get_status():
    return {"status": "online", "system": "ZenSynora Web UI"}


@app.get("/api/agents")
async def get_agents():
    config = load_config()
    defaults = config.agents.defaults
    agents = [
        {
            "name": "default",
            "model": defaults.model,
            "system_prompt": getattr(defaults, "system_prompt", ""),
        }
    ]
    for na in config.agents.named:
        agents.append({"name": na.name, "model": na.model, "system_prompt": na.system_prompt or ""})
    return {"agents": agents}


@app.get("/api/skills")
async def get_skills():
    return {"skills": list(TOOLS.keys())}


# ── Admin Dashboard API ─────────────────────────────────────────────────────
@app.get("/api/admin/dashboard", dependencies=[Depends(require_admin_api_key)])
async def admin_dashboard():
    """Admin dashboard data: sessions, routing, KB stats, provider health."""
    return build_dashboard_data()


@app.get("/api/admin/costs", dependencies=[Depends(require_admin_api_key)])
async def admin_costs(month: str = None):
    """LLM cost tracking: monthly costs by provider."""
    return {
        "month": month or "current",
        "monthly": get_monthly_costs(month),
        "summary": get_cost_summary(),
    }


# ── Knowledge Spaces API ────────────────────────────────────────────────────
@app.post("/api/spaces", dependencies=[Depends(require_admin_api_key)])
async def api_create_space(name: str, owner: str, description: str = ""):
    sid = create_space(name=name, owner=owner, description=description)
    return {"space_id": sid}


@app.get("/api/spaces", dependencies=[Depends(require_admin_api_key)])
async def api_list_spaces(user_id: str):
    return {"spaces": list_spaces(user_id)}


@app.get("/api/spaces/{space_id}", dependencies=[Depends(require_admin_api_key)])
async def api_get_space(space_id: str):
    space = get_space(space_id)
    if not space:
        return {"error": "Space not found"}, 404
    return space


@app.post("/api/spaces/{space_id}/members", dependencies=[Depends(require_admin_api_key)])
async def api_add_member(space_id: str, user_id: str, role: str, added_by: str):
    if add_member(space_id, user_id, role, added_by):
        return {"status": "added"}
    return {"error": "Not authorized or invalid role"}, 403


@app.delete(
    "/api/spaces/{space_id}/members/{user_id}", dependencies=[Depends(require_admin_api_key)]
)
async def api_remove_member(space_id: str, user_id: str, removed_by: str):
    if remove_member(space_id, user_id, removed_by):
        return {"status": "removed"}
    return {"error": "Not authorized"}, 403


# ── MFA / TOTP Authentication ────────────────────────────────────────────────


@app.post("/api/mfa/setup", dependencies=[Depends(require_admin_api_key)])
async def mfa_setup(user_id: str):
    """Provision MFA for a user. Returns provisioning URI and QR code."""
    if not _mfa.is_available():
        return {"error": "MFA not available. Install: pip install pyotp"}, 503
    result = _mfa.provision_user(user_id)
    return result


@app.post("/api/mfa/verify", dependencies=[Depends(require_admin_api_key)])
async def mfa_verify(user_id: str, code: str):
    """Verify a TOTP code for a user."""
    if not _mfa.is_available():
        return {"error": "MFA not available"}, 503
    ok = _mfa.verify(user_id, code)
    return {"valid": ok}


@app.post("/api/mfa/disable", dependencies=[Depends(require_admin_api_key)])
async def mfa_disable(user_id: str):
    """Disable MFA for a user."""
    if not _mfa.is_available():
        return {"error": "MFA not available"}, 503
    _mfa.disable_user(user_id)
    return {"status": "disabled"}


@app.get("/api/mfa/status", dependencies=[Depends(require_admin_api_key)])
async def mfa_status(user_id: str):
    """Check if MFA is enabled for a user."""
    enabled = _mfa.is_enabled_for_user(user_id)
    return {"enabled": enabled, "available": _mfa.is_available()}


# ── Metering API ─────────────────────────────────────────────────────────────
@app.get("/api/metering/status", dependencies=[Depends(require_admin_api_key)])
async def metering_status(user_id: str):
    """Get usage and quota status for a user."""
    from myclaw.metering import get_user_summary

    return get_user_summary(user_id)


@app.post("/api/metering/quota", dependencies=[Depends(require_admin_api_key)])
async def metering_set_quota(user_id: str, quota_name: str, limit_value: int):
    """Set a quota limit for a user."""
    from myclaw.metering import set_quota

    set_quota(user_id, quota_name, limit_value)
    return {"status": "quota_set", "user_id": user_id, "quota": quota_name, "limit": limit_value}


# ── WebSocket Chat with Streaming ────────────────────────────────────────────


@app.websocket("/ws/chat/{agent_name}")
async def chat_websocket(websocket: WebSocket, agent_name: str):
    await websocket.accept()
    session_id = str(uuid.uuid4())
    register_websocket_session(session_id)

    # ── MFA check ────────────────────────────────────────────────────────────
    # If MFA is enabled for user "webui", first message must be "__MFA__:<code>"
    mfa_verified = False
    if not _mfa.is_enabled_for_user("webui"):
        mfa_verified = True  # MFA not required

    registry = _ensure_registry()
    agent = registry.get(agent_name)
    if agent is None:
        await websocket.send_text("__ERROR__: Agent not found.")
        await websocket.close()
        unregister_websocket_session(session_id)
        return

    try:
        while True:
            data = await websocket.receive_text()
            update_session_activity(session_id)

            # Heartbeat ping/pong
            if data == "__ping__":
                await websocket.send_text("__pong__")
                continue

            # ── MFA verification ─────────────────────────────────────────────
            if not mfa_verified:
                if data.startswith("__MFA__:"):
                    code = data.split(":", 1)[1].strip()
                    if _mfa.verify("webui", code):
                        mfa_verified = True
                        await websocket.send_text("__MFA_OK__")
                    else:
                        await websocket.send_text("__MFA_FAIL__")
                        await websocket.close()
                        unregister_websocket_session(session_id)
                        return
                else:
                    await websocket.send_text("__MFA_REQUIRED__")
                continue

            # ── Quota check ──────────────────────────────────────────────────
            allowed, remaining = check_quota("webui", "llm_requests_daily")
            if not allowed:
                await websocket.send_text("__QUOTA_EXCEEDED__: Daily LLM request limit reached.")
                continue

            logger.info(f"Received WS message for {agent_name}: {data[:80]}...")

            # Signal the start of streaming
            await websocket.send_text("__STREAM_START__")

            start_time = time.time()
            try:
                async for chunk in agent.stream_think(data, user_id="webui"):
                    # Skip the internal tool-call marker; send a clean end signal
                    if chunk == "[TOOL_CALLS_NONE]":
                        await websocket.send_text("__STREAM_END__")
                        continue

                    # Skip empty chunks
                    if chunk:
                        await websocket.send_text(chunk)

                # Ensure end signal is sent if not already
                await websocket.send_text("__STREAM_END__")
                record_response_time(time.time() - start_time)

            except Exception as e:
                logger.error(f"Error during streaming for {agent_name}: {e}")
                await websocket.send_text(f"\n\n[Error: {e}]")
                await websocket.send_text("__STREAM_END__")
                record_response_time(time.time() - start_time)

    except Exception as e:
        logger.error(f"WebSocket Error: {e}")
        await websocket.close()
    finally:
        unregister_websocket_session(session_id)


# ── Static File Serving (built React frontend) ──────────────────────────────
# The frontend is built to webui/dist/ via `npm run build` in the webui/ directory.
# In Docker, the built files are copied to ./webui/dist/ in the container.
_DIST_DIR = Path(__file__).parent.parent.parent / "webui" / "dist"

if _DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA — return index.html for all non-API routes."""
        # API routes are handled above; this catch-all serves the frontend
        index_file = _DIST_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"error": "Frontend not built. Run `cd webui && npm run build`."}
