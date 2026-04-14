import logging
from typing import Any, TYPE_CHECKING
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

if TYPE_CHECKING:
    from .dashboard import MyClawDashboard

logger = logging.getLogger(__name__)

def create_dashboard_app(dashboard: "MyClawDashboard") -> FastAPI:
    """Create the FastAPI application for the dashboard."""
    from .dashboard import DASHBOARD_HTML
    
    app = FastAPI(title="MyClaw Admin Dashboard")
    
    @app.get("/", response_class=HTMLResponse)
    async def root():
        return DASHBOARD_HTML
    
    @app.get("/api/overview")
    async def api_overview():
        return JSONResponse(dashboard.get_overview_data())
    
    @app.get("/api/agents")
    async def api_agents():
        return JSONResponse(dashboard.get_agent_data())
    
    @app.delete("/api/agents/{agent_name}")
    async def api_remove_agent(agent_name: str):
        return JSONResponse({"success": True})
    
    @app.get("/api/swarms")
    async def api_swarms():
        return JSONResponse(dashboard.get_swarm_data())
    
    @app.post("/api/swarms/{swarm_id}")
    async def api_terminate_swarm(swarm_id: str):
        return JSONResponse({"success": True})
    
    @app.get("/api/memory")
    async def api_memory():
        return JSONResponse(dashboard.get_memory_data())
    
    @app.get("/api/config")
    async def api_config():
        return JSONResponse(dashboard.get_config_data())
    
    @app.get("/api/logs")
    async def api_logs():
        return JSONResponse(dashboard.get_log_data())
    
    return app
