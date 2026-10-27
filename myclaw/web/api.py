from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
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
            key=nc.key or None,  # v0.7.5: which API key of the provider
        )

    load_custom_tools()
    set_registry(registry)
    _agent_registry = registry
    logger.info(f"Web UI agent registry built: {list(registry.keys())}")
    return registry


from myclaw import __version__ as _APP_VERSION  # noqa: E402

app = FastAPI(title="ZenSynora Web UI Backend", version=_APP_VERSION)


async def _close_async_db_pool() -> None:
    # Pooled aiosqlite connections use non-daemon threads; close them so the
    # server process can exit after Ctrl+C instead of hanging.
    from myclaw.memory import cleanup_on_shutdown
    await cleanup_on_shutdown()


app.router.add_event_handler("shutdown", _close_async_db_pool)


async def _reset_agent_registry() -> None:
    """Drop cached agents so the next chat uses freshly saved settings."""
    global _agent_registry
    old = _agent_registry
    _agent_registry = {}
    for agent in old.values():
        try:
            await agent.close()
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("Error closing agent during registry reset: %s", exc)
    # v0.7.5: an agent renamed or deleted in Settings leaves its channels at once
    # (it used to stay a "zombie" member that blocked every member change).
    try:
        _chat_api.sync_channels()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Could not update channels after the settings change: %s", exc)
    # Telegram bot in the Web UI: restart it only when its settings changed
    # (it reads agents through _ensure_registry, so the new registry is used).
    try:
        await _telegram_api.on_settings_changed()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Could not apply the Telegram settings: %s", exc)


from myclaw.web import settings_api as _settings_api  # noqa: E402

_settings_api.set_config_changed_hook(_reset_agent_registry)
app.include_router(_settings_api.router)

# v0.7.1: model switching/rotation + prompt generator, multi-agent chat
# channels, jobs, backups and medic self-update proposals.
from myclaw.web import models_api as _models_api  # noqa: E402
from myclaw.web import chat_api as _chat_api  # noqa: E402
from myclaw.web import jobs_api as _jobs_api  # noqa: E402
from myclaw.web import backup_api as _backup_api  # noqa: E402
from myclaw.web import medic_api as _medic_api  # noqa: E402
from myclaw.web import update_api as _update_api  # noqa: E402
from myclaw.web import skills_api as _skills_api  # noqa: E402
from myclaw.web import telegram_api as _telegram_api  # noqa: E402
from myclaw.web import doctor_api as _doctor_api  # noqa: E402
from myclaw.web import knowledge_api as _knowledge_api  # noqa: E402

_chat_api.set_registry_getter(_ensure_registry)
# The bot gets the getter (not the dict): the registry is rebuilt after each save.
_telegram_api.set_registry_getter(_ensure_registry)


def _add_created_agent(name: str, agent) -> None:
    """v0.7.5: an agent made with create_agent joins the Web UI at once."""
    if _agent_registry:
        _agent_registry[name] = agent


from myclaw import agent_team as _agent_team  # noqa: E402

_agent_team.on_agent_created(_add_created_agent)
_jobs_api.set_registry_getter(_ensure_registry, _chat_api.engine)
_backup_api.set_restore_hook(_reset_agent_registry)  # restored settings take effect at once
app.include_router(_models_api.router)
app.include_router(_chat_api.router)
app.include_router(_jobs_api.router)
app.include_router(_backup_api.router)
app.include_router(_medic_api.router)  # self-update proposals (v0.7.1)
app.include_router(_update_api.router)  # check / update / rollback / restart (v0.7.4)
app.include_router(_skills_api.router)  # agent skills (v0.7.5)
app.include_router(_telegram_api.router)  # Telegram bot inside the Web UI
app.include_router(_doctor_api.router)  # doctor report, errors viewer, download
app.include_router(_knowledge_api.router)  # knowledge base explorer (graph, notes, tags)
from myclaw.web import knowledge_import_api as _knowledge_import_api  # noqa: E402
app.include_router(_knowledge_import_api.router)  # 2026-10-24 (KB phase 5): assimilate / import jobs
from myclaw.web import knowledge_semantic_api as _knowledge_semantic_api  # noqa: E402
app.include_router(_knowledge_semantic_api.router)  # 2026-10-24 (KB phase 3): semantic search status / backfill / install
from myclaw.web import knowledge_distill_api as _knowledge_distill_api  # noqa: E402
app.include_router(_knowledge_distill_api.router)  # 2026-10-24 (KB phase 4): facts / scenarios / persona / drill-down
from myclaw.web import knowledge_health_api as _knowledge_health_api  # noqa: E402
app.include_router(_knowledge_health_api.router)  # 2026-10-25 (KB phase 6): index health, file watcher status
from myclaw.web import knowledge_usage_api as _knowledge_usage_api  # noqa: E402
app.include_router(_knowledge_usage_api.router)  # 2026-10-27: usage log (which agent used what, why), benchmarks


_mcp_manager = None


async def _start_background_runner() -> None:
    """Job scheduler + automatic backups run inside the Web UI process."""
    _jobs_api.start_runner()
    # v0.7.5: external MCP servers (settings mcp.servers — e.g. a memory/notes
    # server) used to be started only by `zensynora agent` and the gateway, so
    # their tools were missing in the Web UI.
    global _mcp_manager
    try:
        cfg = load_config()
        if cfg.mcp.enabled and cfg.mcp.servers:
            from myclaw.mcp import MCPClientManager

            _mcp_manager = MCPClientManager(cfg.model_dump())
            await _mcp_manager.start_all()
    except Exception as exc:  # an MCP server must never stop the Web UI
        logger.warning("Could not start MCP servers: %s", exc)
    # Telegram bot (channels.telegram.run_in_webui): no separate gateway needed.
    try:
        await _telegram_api.start_bot()
    except Exception as exc:  # the bot must never stop the Web UI
        logger.warning("Could not start the Telegram bot: %s", exc.__class__.__name__)


async def _stop_background_runner() -> None:
    try:
        await _telegram_api.stop_bot()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Could not stop the Telegram bot: %s", exc)
    await _jobs_api.stop_runner()
    if _mcp_manager is not None:
        await _mcp_manager.stop_all()


app.router.add_event_handler("startup", _start_background_runner)


async def _start_semantic_scans() -> None:
    """2026-10-24 (KB phase 3 review): the Web UI is the long-running process
    that embeds notes still waiting (written by CLI commands, while the model
    could not load, before the switch was on) — now and every 10 minutes,
    only while semantic search is on."""
    try:
        from myclaw.knowledge import semantic

        semantic.start_background()
    except Exception as exc:  # pragma: no cover - never block the start
        logger.warning("Semantic search background scans not started: %s", exc)


app.router.add_event_handler("startup", _start_semantic_scans)


async def _start_distill_scans() -> None:
    """2026-10-24 (KB phase 4): the Web UI distils changed notes in the
    background (after knowledge.distill_every changed notes or when idle; also
    notes written by CLI commands) — only while knowledge.distill is on. Runs
    on its own thread; waits while the semantic worker embeds."""
    try:
        from myclaw.knowledge import distill

        distill.start_background()
    except Exception as exc:  # pragma: no cover - never block the start
        logger.warning("Knowledge distillation scheduler not started: %s", exc)


app.router.add_event_handler("startup", _start_distill_scans)


async def _start_file_watcher() -> None:
    """2026-10-25 (KB phase 6): the Web UI re-indexes note files changed by hand
    (editor, Obsidian, git pull) — only while knowledge.watch_files is on; the
    thread starts and stops with the switch."""
    try:
        from myclaw.knowledge import watch

        watch.start_background()
    except Exception as exc:  # pragma: no cover - never block the start
        logger.warning("Knowledge file watcher not started: %s", exc)


async def _stop_file_watcher() -> None:
    try:
        from myclaw.knowledge import watch

        await asyncio.to_thread(watch.stop_background)
    except Exception as exc:  # pragma: no cover - best effort
        logger.debug("Knowledge file watcher not stopped: %s", exc)


app.router.add_event_handler("startup", _start_file_watcher)
app.router.add_event_handler("shutdown", _stop_file_watcher)
app.router.add_event_handler("shutdown", _stop_background_runner)

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
    return {"status": "healthy", "service": "zensynora-webui", "version": _APP_VERSION}


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
            "provider": defaults.provider,
            "system_prompt": getattr(defaults, "system_prompt", ""),
        }
    ]
    for na in config.agents.named:
        agents.append({
            "name": na.name,
            "model": na.model,
            "provider": na.provider or defaults.provider,
            "system_prompt": na.system_prompt or "",
            "description": getattr(na, "description", "") or "",
        })
    return {"agents": agents}


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
    _settings_api.ws_opened()

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
        _settings_api.ws_closed()
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

            # Re-resolve per message: saving settings rebuilds the registry,
            # and an open socket must pick up the new provider/model.
            agent = _ensure_registry().get(agent_name) or agent

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
                # v0.7.3: a browser that closed the tab mid-answer is not an
                # error (it also logged "Cannot call send once a close message
                # has been sent"); and real errors get a readable reason.
                from starlette.websockets import WebSocketState

                if websocket.client_state != WebSocketState.CONNECTED or "close message" in str(e):
                    logger.debug("Client left during streaming for %s", agent_name)
                    break
                from myclaw.llm_errors import describe_llm_error

                logger.error(f"Error during streaming for {agent_name}: {e}")
                await websocket.send_text(f"\n\n[Error] {describe_llm_error(e)}")
                await websocket.send_text("__STREAM_END__")
                record_response_time(time.time() - start_time)

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected (%s)", agent_name)
    except Exception as e:
        if "close message" in str(e):  # client already gone (v0.7.3)
            logger.debug("WebSocket closed by the client (%s)", agent_name)
        else:
            logger.error(f"WebSocket Error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        unregister_websocket_session(session_id)
        _settings_api.ws_closed()


# ── Static File Serving (built React frontend) ──────────────────────────────
# The frontend is built to webui/dist/ via `npm run build` in the webui/ directory.
# In Docker, the built files are copied to ./webui/dist/ in the container.
_DIST_DIR = Path(__file__).parent.parent.parent / "webui" / "dist"

if _DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA — return index.html for all non-API routes.

        Changed 2026-10-14 (v0.7.2):
        * index.html is sent with ``Cache-Control: no-cache`` so the browser
          picks up a new build after an update (the hashed /assets files can
          still be cached forever) — before, an updated Web UI could keep
          showing the previous version;
        * unknown ``/api/…`` and ``/ws/…`` paths answer 404 instead of the
          HTML page with status 200, which hid missing endpoints;
        * real files in dist/ (favicon.svg, icons.svg) are served as files.
        """
        from fastapi.responses import JSONResponse

        if full_path.startswith(("api/", "ws/")) or full_path in ("api", "ws"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (_DIST_DIR / full_path).resolve()
        if full_path and candidate.is_file() and _DIST_DIR.resolve() in candidate.parents:
            return FileResponse(str(candidate))
        index_file = _DIST_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file), headers={"Cache-Control": "no-cache, must-revalidate"})
        return {"error": "Frontend not built. Run `cd webui && npm run build`."}
