from pathlib import Path
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import asyncio
import logging

from myclaw.config import load_config
from myclaw.tools import TOOLS

logger = logging.getLogger(__name__)

app = FastAPI(title="ZenSynora Web UI Backend", version="1.0.0")

# Enable CORS for the Vite React server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    agents = [{"name": "default", "model": defaults.model, "system_prompt": getattr(defaults, 'system_prompt', '')}]
    for na in config.agents.named:
        agents.append({"name": na.name, "model": na.model, "system_prompt": na.system_prompt or ''})
    return {"agents": agents}

@app.get("/api/skills")
async def get_skills():
    return {"skills": list(TOOLS.keys())}

@app.websocket("/ws/chat/{agent_name}")
async def chat_websocket(websocket: WebSocket, agent_name: str):
    await websocket.accept()
    # In a full production implementation, we would instantiate the requested agent here
    # and hook into its think_stream() generator. For MVP, we will send a basic response mechanism.
    try:
        while True:
            data = await websocket.receive_text()

            # Heartbeat ping/pong
            if data == '__ping__':
                await websocket.send_text('__pong__')
                continue

            logger.info(f"Received WS message for {agent_name}: {data}")

            # Simulated Agent Delay & Stream
            await asyncio.sleep(0.5)
            response = f"**{agent_name.capitalize()}**: I have received your message regarding '{data}'. This is currently a simulated response from the Web UI backend. Full integration with `Agent.think()` will be mapped here."
            await websocket.send_text(response)
    except Exception as e:
        logger.error(f"WebSocket Error: {e}")
        await websocket.close()


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
