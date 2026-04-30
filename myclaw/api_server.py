"""
REST API Server for External Integration

Provides a comprehensive REST API for external integrations:
- Agent management endpoints
- Tool execution API
- Swarm control endpoints
- Memory/knowledge operations
- WebSocket support for real-time
- Authentication and rate limiting
- API key management
"""

import asyncio
import hashlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from fastapi import FastAPI, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

API_CONFIG_DIR = Path.home() / ".myclaw" / "api"
API_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
API_KEYS_FILE = API_CONFIG_DIR / "api_keys.json"
RATE_LIMITS_FILE = API_CONFIG_DIR / "rate_limits.json"


@dataclass
class APIKey:
    """API key information."""
    key: str
    name: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    permissions: List[str] = field(default_factory=list)
    rate_limit: int = 100
    rate_window: int = 60
    active: bool = True


@dataclass
class RateLimitEntry:
    """Rate limit tracking entry."""
    count: int = 0
    window_start: float = 0.0


class APIServer:
    """REST API server for MyClaw.
    
    Features:
    - Agent management endpoints
    - Tool execution API
    - Swarm control endpoints
    - Memory/knowledge operations
    - WebSocket for real-time
    - Authentication and rate limiting
    - API key management
    """
    
    def __init__(
        self,
        agent_registry: Optional[Dict[str, Any]] = None,
        swarm_orchestrator=None,
        memory=None,
        host: str = "0.0.0.0",
        port: int = 8000,
        cors_origins: Optional[List[str]] = None,
    ):
        self._agent_registry = agent_registry or {}
        self._swarm_orchestrator = swarm_orchestrator
        self._memory = memory
        self._host = host
        self._port = port
        self._app: Optional[FastAPI] = None
        self._api_keys = self._load_api_keys()
        self._rate_limits: Dict[str, RateLimitEntry] = {}
        self._websocket_connections: List[WebSocket] = []
        self._middleware: List[Callable] = []
        # Explicit allow-list of CORS origins. Defaults to localhost dev server.
        # MUST be overridden in production via config.security.cors_origins.
        self._cors_origins: List[str] = cors_origins or ["http://localhost:5173"]
    
    def _load_api_keys(self) -> Dict[str, APIKey]:
        """Load API keys from disk."""
        if API_KEYS_FILE.exists():
            try:
                import json
                data = json.loads(API_KEYS_FILE.read_text())
                return {
                    k: APIKey(
                        key=k,
                        name=v["name"],
                        created_at=datetime.fromisoformat(v["created_at"]),
                        expires_at=datetime.fromisoformat(v["expires_at"]) if v.get("expires_at") else None,
                        permissions=v.get("permissions", []),
                        rate_limit=v.get("rate_limit", 100),
                        rate_window=v.get("rate_window", 60),
                        active=v.get("active", True)
                    )
                    for k, v in data.items()
                }
            except Exception as e:
                logger.error(f"Failed to load API keys: {e}")
        return {}
    
    def _save_api_keys(self):
        """Save API keys to disk."""
        import json
        data = {
            k: {
                "name": v.name,
                "created_at": v.created_at.isoformat(),
                "expires_at": v.expires_at.isoformat() if v.expires_at else None,
                "permissions": v.permissions,
                "rate_limit": v.rate_limit,
                "rate_window": v.rate_window,
                "active": v.active
            }
            for k, v in self._api_keys.items()
        }
        API_KEYS_FILE.write_text(json.dumps(data, indent=2))
    
    def generate_api_key(self, name: str, permissions: Optional[List[str]] = None) -> str:
        """Generate a new API key.
        
        Args:
            name: Name/identifier for the key
            permissions: List of permission scopes
            
        Returns:
            The generated API key
        """
        key = secrets.token_urlsafe(32)
        
        self._api_keys[key] = APIKey(
            key=key,
            name=name,
            created_at=datetime.now(),
            permissions=permissions or ["read", "execute"],
            rate_limit=100,
            rate_window=60
        )
        
        self._save_api_keys()
        logger.info(f"Generated API key for: {name}")
        
        return key
    
    def revoke_api_key(self, key: str) -> bool:
        """Revoke an API key."""
        if key in self._api_keys:
            del self._api_keys[key]
            self._save_api_keys()
            return True
        return False
    
    def _check_rate_limit(self, key: str) -> bool:
        """Check if request is within rate limit.
        
        Returns:
            True if within limit, False if exceeded
        """
        if key not in self._api_keys:
            return True
        
        api_key = self._api_keys[key]
        current_time = time.time()
        
        if key not in self._rate_limits:
            self._rate_limits[key] = RateLimitEntry(
                count=1,
                window_start=current_time
            )
            return True
        
        entry = self._rate_limits[key]
        
        if current_time - entry.window_start > api_key.rate_window:
            entry.count = 1
            entry.window_start = current_time
            return True
        
        if entry.count >= api_key.rate_limit:
            return False
        
        entry.count += 1
        return True
    
    async def _verify_api_key(self, request: Request) -> Optional[str]:
        """Verify API key from request headers."""
        auth_header = request.headers.get("Authorization", "")
        
        if auth_header.startswith("Bearer "):
            key = auth_header[7:]
        else:
            key = request.headers.get("X-API-Key", "")
        
        if not key:
            return None
        
        if key in self._api_keys:
            api_key_obj = self._api_keys[key]
            
            if not api_key_obj.active:
                raise HTTPException(status_code=401, detail="API key inactive")
            
            if api_key_obj.expires_at and datetime.now() > api_key_obj.expires_at:
                raise HTTPException(status_code=401, detail="API key expired")
            
            if not self._check_rate_limit(key):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            
            return key
        
        return None
    
    def create_app(self) -> FastAPI:
        """Create the FastAPI application."""
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            logger.info("API Server starting up")
            yield
            logger.info("API Server shutting down")
        
        app = FastAPI(
            title="MyClaw API",
            description="REST API for MyClaw AI Agent",
            version="0.2.0",
            lifespan=lifespan
        )
        
        # SECURITY: Never combine allow_origins=["*"] with allow_credentials=True.
        # Use an explicit allow-list configured via APIServer(cors_origins=...).
        app.add_middleware(
            CORSMiddleware,
            allow_origins=self._cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "X-API-Key", "Content-Type"],
        )
        
        @app.get("/")
        async def root():
            return {
                "name": "MyClaw API",
                "version": "0.2.0",
                "status": "running"
            }
        
        @app.get("/health")
        async def health():
            return {"status": "healthy", "timestamp": datetime.now().isoformat()}
        
        @app.get("/api/v1/agents")
        async def list_agents(api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))):
            agents = []
            for name, agent in self._agent_registry.items():
                agents.append({
                    "name": name,
                    "type": agent.__class__.__name__ if hasattr(agent, '__class__') else "unknown",
                    "status": "online"
                })
            return {"agents": agents, "count": len(agents)}
        
        @app.post("/api/v1/agents/{agent_name}/execute")
        async def execute_agent(
            agent_name: str,
            request: Request,
            api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))
        ):
            body = await request.json()
            message = body.get("message", "")
            
            if agent_name not in self._agent_registry:
                raise HTTPException(status_code=404, detail=f"Agent not found: {agent_name}")
            
            agent = self._agent_registry[agent_name]
            
            if hasattr(agent, 'think'):
                result = await agent.think(message)
                return {"result": result, "agent": agent_name}
            else:
                raise HTTPException(status_code=400, detail="Agent does not support execution")
        
        # ── Cost dashboard endpoints ─────────────────────────────────
        # All require an authenticated key but not admin-only — readers
        # of their own usage shouldn't need full admin powers.

        @app.get("/api/v1/costs/summary")
        async def costs_summary(api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))):
            from myclaw.cost_tracker import get_cost_summary
            if not api_key:
                raise HTTPException(status_code=401, detail="Authentication required")
            return get_cost_summary()

        @app.get("/api/v1/costs/by-provider")
        async def costs_by_provider(
            month: Optional[str] = None,
            api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r)),
        ):
            from myclaw.cost_tracker import get_monthly_costs
            if not api_key:
                raise HTTPException(status_code=401, detail="Authentication required")
            return {"month": month, "rows": get_monthly_costs(month)}

        @app.get("/api/v1/costs/by-model")
        async def costs_by_model(
            month: Optional[str] = None,
            limit: int = 20,
            api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r)),
        ):
            from myclaw.cost_tracker import get_costs_by_model
            if not api_key:
                raise HTTPException(status_code=401, detail="Authentication required")
            return {"month": month, "rows": get_costs_by_model(month, limit=limit)}

        @app.get("/api/v1/costs/timeline")
        async def costs_timeline(
            days: int = 30,
            api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r)),
        ):
            from myclaw.cost_tracker import get_daily_timeline
            if not api_key:
                raise HTTPException(status_code=401, detail="Authentication required")
            return {"days": days, "rows": get_daily_timeline(days)}

        @app.get("/api/v1/tools")
        async def list_tools(api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))):
            from myclaw.tools import TOOL_SCHEMAS, ensure_tool_schemas
            ensure_tool_schemas()
            return {"tools": TOOL_SCHEMAS, "count": len(TOOL_SCHEMAS)}
        
        @app.post("/api/v1/tools/execute")
        async def execute_tool(
            request: Request,
            api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))
        ):
            body = await request.json()
            tool_name = body.get("tool")
            params = body.get("params", {})
            
            from myclaw.tools import TOOL_FUNCTIONS
            
            if tool_name not in TOOL_FUNCTIONS:
                raise HTTPException(status_code=404, detail=f"Tool not found: {tool_name}")
            
            tool_func = TOOL_FUNCTIONS[tool_name]
            
            try:
                result = await tool_func(**params) if asyncio.iscoroutinefunction(tool_func) else tool_func(**params)
                return {"result": result, "tool": tool_name}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        if self._swarm_orchestrator:
            @app.get("/api/v1/swarms")
            async def list_swarms(api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))):
                swarms = self._swarm_orchestrator.list_swarms()
                return {
                    "swarms": [
                        {
                            "id": s.id,
                            "name": s.name,
                            "strategy": s.strategy.value,
                            "status": s.status.value
                        }
                        for s in swarms
                    ]
                }
            
            @app.post("/api/v1/swarms")
            async def create_swarm(request: Request, api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))):
                body = await request.json()
                from myclaw.swarm.models import SwarmConfig, SwarmStrategy, AggregationMethod
                
                config = SwarmConfig(
                    name=body["name"],
                    strategy=SwarmStrategy(body.get("strategy", "parallel")),
                    workers=body["workers"],
                    coordinator=body.get("coordinator"),
                    aggregation_method=AggregationMethod(body.get("aggregation", "synthesis"))
                )
                
                swarm_id = await self._swarm_orchestrator.create_swarm(
                    config,
                    user_id=body.get("user_id", "default")
                )
                
                return {"swarm_id": swarm_id, "name": body["name"]}
            
            @app.post("/api/v1/swarms/{swarm_id}/execute")
            async def execute_swarm(
                swarm_id: str,
                request: Request,
                api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))
            ):
                body = await request.json()
                result = await self._swarm_orchestrator.execute_task(
                    swarm_id,
                    body["task"],
                    body.get("input_data"),
                    body.get("timeout")
                )
                
                return {
                    "swarm_id": swarm_id,
                    "result": result.final_result,
                    "confidence": result.confidence_score,
                    "execution_time": result.execution_time_seconds
                }
        
        @app.get("/api/v1/memory/history")
        async def get_memory_history(
            limit: int = 50,
            api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))
        ):
            if self._memory:
                history = await self._memory.get_history(limit)
                return {"history": history}
            return {"history": []}
        
        @app.post("/api/v1/memory/add")
        async def add_memory(
            request: Request,
            api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))
        ):
            body = await request.json()
            
            if self._memory:
                await self._memory.add(
                    body["role"],
                    body["content"],
                    body.get("user_id", "default")
                )
                return {"status": "added"}
            return {"status": "error", "message": "Memory not configured"}
        
        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            self._websocket_connections.append(websocket)
            
            try:
                while True:
                    data = await websocket.receive_json()
                    
                    await self._handle_websocket_message(websocket, data)
                    
            except WebSocketDisconnect:
                self._websocket_connections.remove(websocket)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self._websocket_connections.remove(websocket)
        
        def _require_admin(api_key: Optional[str]) -> None:
            """Raise 401/403 unless the caller is authenticated with admin permission."""
            if not api_key:
                raise HTTPException(status_code=401, detail="Authentication required")
            key_obj = self._api_keys.get(api_key)
            if key_obj is None or "admin" not in key_obj.permissions:
                raise HTTPException(status_code=403, detail="Admin permission required")

        @app.get("/api/v1/keys")
        async def list_api_keys(api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))):
            _require_admin(api_key)
            return {
                "keys": [
                    {
                        "name": v.name,
                        "created_at": v.created_at.isoformat(),
                        "expires_at": v.expires_at.isoformat() if v.expires_at else None,
                        "permissions": v.permissions,
                        "active": v.active
                    }
                    for v in self._api_keys.values()
                ]
            }

        @app.post("/api/v1/keys")
        async def create_api_key(
            request: Request,
            api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))
        ):
            _require_admin(api_key)
            body = await request.json()

            new_key = self.generate_api_key(
                body["name"],
                body.get("permissions")
            )

            return {"key": new_key, "name": body["name"]}

        @app.delete("/api/v1/keys/{key_name}")
        async def revoke_key(
            key_name: str,
            api_key: Optional[str] = Depends(lambda r: self._verify_api_key(r))
        ):
            _require_admin(api_key)
            for k, v in self._api_keys.items():
                if v.name == key_name:
                    self.revoke_api_key(k)
                    return {"status": "revoked"}

            raise HTTPException(status_code=404, detail="Key not found")
        
        self._app = app
        return app
    
    async def _handle_websocket_message(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle incoming WebSocket message."""
        msg_type = data.get("type")
        
        if msg_type == "ping":
            await websocket.send_json({"type": "pong"})
        
        elif msg_type == "execute":
            if data.get("target") == "agent":
                agent_name = data.get("agent")
                if agent_name in self._agent_registry:
                    agent = self._agent_registry[agent_name]
                    if hasattr(agent, 'think'):
                        result = await agent.think(data.get("message", ""))
                        await websocket.send_json({
                            "type": "result",
                            "success": True,
                            "result": result
                        })
            
            elif data.get("target") == "broadcast":
                for conn in self._websocket_connections:
                    if conn != websocket:
                        await conn.send_json({
                            "type": "broadcast",
                            "from": data.get("from", "unknown"),
                            "message": data.get("message", "")
                        })
    
    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all WebSocket connections."""
        for conn in self._websocket_connections:
            try:
                await conn.send_json(message)
            except Exception:
                pass
    
    async def start(self):
        """Start the API server."""
        import uvicorn
        
        if self._app is None:
            self.create_app()
        
        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="info"
        )
        server = uvicorn.Server(config)
        await server.serve()


def create_api_server(
    agent_registry: Optional[Dict[str, Any]] = None,
    swarm_orchestrator=None,
    memory=None,
    host: str = "0.0.0.0",
    port: int = 8000
) -> APIServer:
    """Create an API server instance."""
    return APIServer(
        agent_registry=agent_registry,
        swarm_orchestrator=swarm_orchestrator,
        memory=memory,
        host=host,
        port=port
    )


async def run_api_server(**kwargs):
    """Run API server with default configuration."""
    server = create_api_server(**kwargs)
    await server.start()


__all__ = [
    "APIKey",
    "RateLimitEntry",
    "APIServer",
    "create_api_server",
    "run_api_server",
]