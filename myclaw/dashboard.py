"""
Web Dashboard for MyClaw Admin UI

Provides a web-based administrative interface for:
- Agent management and monitoring
- Swarm status and control
- Memory and cache statistics
- Configuration management
- Usage analytics
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MyClaw Admin Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; }
        .header { background: #1e293b; padding: 1rem 2rem; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center; }
        .header h1 { font-size: 1.5rem; color: #38bdf8; }
        .tabs { display: flex; gap: 0.5rem; padding: 1rem 2rem; background: #1e293b; border-bottom: 1px solid #334155; }
        .tab { padding: 0.5rem 1rem; border-radius: 0.5rem; background: transparent; color: #94a3b8; cursor: pointer; border: none; transition: all 0.2s; }
        .tab:hover { background: #334155; color: #e2e8f0; }
        .tab.active { background: #38bdf8; color: #0f172a; }
        .content { padding: 2rem; }
        .card { background: #1e293b; border-radius: 0.75rem; padding: 1.5rem; margin-bottom: 1rem; border: 1px solid #334155; }
        .card h2 { font-size: 1.25rem; margin-bottom: 1rem; color: #38bdf8; }
        .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }
        .stat { background: #0f172a; padding: 1rem; border-radius: 0.5rem; text-align: center; }
        .stat-value { font-size: 2rem; font-weight: bold; color: #38bdf8; }
        .stat-label { color: #94a3b8; font-size: 0.875rem; margin-top: 0.5rem; }
        .agent-list { display: grid; gap: 0.75rem; }
        .agent-item { display: flex; justify-content: space-between; align-items: center; padding: 1rem; background: #0f172a; border-radius: 0.5rem; }
        .agent-info { display: flex; align-items: center; gap: 1rem; }
        .status-dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: #22c55e; }
        .status-dot.offline { background: #ef4444; }
        .btn { padding: 0.5rem 1rem; border-radius: 0.5rem; border: none; cursor: pointer; transition: all 0.2s; }
        .btn-primary { background: #38bdf8; color: #0f172a; }
        .btn-danger { background: #ef4444; color: white; }
        .table { width: 100%; border-collapse: collapse; }
        .table th, .table td { padding: 0.75rem; text-align: left; border-bottom: 1px solid #334155; }
        .table th { color: #94a3b8; font-weight: 500; }
        .logs { background: #0f172a; padding: 1rem; border-radius: 0.5rem; max-height: 400px; overflow-y: auto; font-family: monospace; }
        .log-entry { padding: 0.25rem 0; color: #94a3b8; }
        .log-entry.error { color: #ef4444; }
        .log-entry.info { color: #38bdf8; }
        .hidden { display: none; }
    </style>
</head>
<body>
    <div class="header">
        <h1>MyClaw Admin Dashboard</h1>
        <div>
            <span id="statusBadge" style="padding: 0.25rem 0.75rem; background: #22c55e; color: white; border-radius: 1rem; font-size: 0.75rem;">Online</span>
        </div>
    </div>
    
    <div class="tabs">
        <button class="tab active" data-tab="overview">Overview</button>
        <button class="tab" data-tab="agents">Agents</button>
        <button class="tab" data-tab="swarms">Swarms</button>
        <button class="tab" data-tab="memory">Memory</button>
        <button class="tab" data-tab="config">Config</button>
        <button class="tab" data-tab="logs">Logs</button>
    </div>
    
    <div class="content">
        <div id="overview" class="tab-content">
            <div class="card">
                <h2>System Statistics</h2>
                <div class="stat-grid">
                    <div class="stat">
                        <div class="stat-value" id="totalAgents">0</div>
                        <div class="stat-label">Active Agents</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" id="totalSwarms">0</div>
                        <div class="stat-label">Swarms</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" id="cacheHitRate">0%</div>
                        <div class="stat-label">Cache Hit Rate</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" id="memoryUsage">0MB</div>
                        <div class="stat-label">Memory Usage</div>
                    </div>
                </div>
            </div>
            
            <div class="card">
                <h2>Recent Activity</h2>
                <table class="table">
                    <thead>
                        <tr><th>Time</th><th>Event</th><th>Details</th></tr>
                    </thead>
                    <tbody id="recentActivity"></tbody>
                </table>
            </div>
        </div>
        
        <div id="agents" class="tab-content hidden">
            <div class="card">
                <h2>Registered Agents</h2>
                <div class="agent-list" id="agentList"></div>
            </div>
        </div>
        
        <div id="swarms" class="tab-content hidden">
            <div class="card">
                <h2>Active Swarms</h2>
                <div class="agent-list" id="swarmList"></div>
            </div>
        </div>
        
        <div id="memory" class="tab-content hidden">
            <div class="card">
                <h2>Semantic Cache</h2>
                <div class="stat-grid">
                    <div class="stat">
                        <div class="stat-value" id="cacheSize">0</div>
                        <div class="stat-label">Cached Items</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" id="cacheHits">0</div>
                        <div class="stat-label">Total Hits</div>
                    </div>
                </div>
            </div>
            <div class="card">
                <h2>Memory History</h2>
                <div id="memoryHistory"></div>
            </div>
        </div>
        
        <div id="config" class="tab-content hidden">
            <div class="card">
                <h2>Configuration</h2>
                <div id="configDisplay"></div>
            </div>
        </div>
        
        <div id="logs" class="tab-content hidden">
            <div class="card">
                <h2>System Logs</h2>
                <div class="logs" id="logOutput"></div>
            </div>
        </div>
    </div>
    
    <script>
        const tabs = document.querySelectorAll('.tab');
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
                document.getElementById(tab.dataset.tab).classList.remove('hidden');
                loadTabData(tab.dataset.tab);
            });
        });
        
        async function loadTabData(tab) {
            try {
                const res = await fetch('/api/' + tab);
                const data = await res.json();
                if (tab === 'overview') updateOverview(data);
                else if (tab === 'agents') updateAgents(data);
                else if (tab === 'swarms') updateSwarms(data);
                else if (tab === 'memory') updateMemory(data);
                else if (tab === 'config') updateConfig(data);
                else if (tab === 'logs') updateLogs(data);
            } catch (e) { console.error(e); }
        }
        
        async function loadOverview() {
            try {
                const res = await fetch('/api/overview');
                updateOverview(await res.json());
            } catch (e) { console.error(e); }
        }
        
        function updateOverview(data) {
            document.getElementById('totalAgents').textContent = data.agents || 0;
            document.getElementById('totalSwarms').textContent = data.swarms || 0;
            document.getElementById('cacheHitRate').textContent = (data.cacheHitRate || 0) + '%';
            document.getElementById('memoryUsage').textContent = data.memoryUsage || '0MB';
            
            const tbody = document.getElementById('recentActivity');
            tbody.innerHTML = (data.recentActivity || []).map(a => 
                `<tr><td>${a.time}</td><td>${a.event}</td><td>${a.details}</td></tr>`
            ).join('') || '<tr><td colspan="3">No recent activity</td></tr>';
        }
        
        function updateAgents(data) {
            const list = document.getElementById('agentList');
            list.innerHTML = (data.agents || []).map(a => `
                <div class="agent-item">
                    <div class="agent-info">
                        <div class="status-dot ${a.status === 'online' ? '' : 'offline'}"></div>
                        <div>
                            <strong>${a.name}</strong>
                            <div style="color: #94a3b8; font-size: 0.875rem;">${a.type}</div>
                        </div>
                    </div>
                    <button class="btn btn-danger" onclick="removeAgent('${a.name}')">Remove</button>
                </div>
            `).join('') || '<p style="color: #94a3b8;">No agents registered</p>';
        }
        
        function updateSwarms(data) {
            const list = document.getElementById('swarmList');
            list.innerHTML = (data.swarms || []).map(s => `
                <div class="agent-item">
                    <div class="agent-info">
                        <div class="status-dot ${s.status === 'running' ? '' : 'offline'}"></div>
                        <div>
                            <strong>${s.name}</strong>
                            <div style="color: #94a3b8; font-size: 0.875rem;">${s.strategy} - ${s.status}</div>
                        </div>
                    </div>
                    <button class="btn btn-danger" onclick="terminateSwarm('${s.id}')">Terminate</button>
                </div>
            `).join('') || '<p style="color: #94a3b8;">No active swarms</p>';
        }
        
        function updateMemory(data) {
            document.getElementById('cacheSize').textContent = data.cacheSize || 0;
            document.getElementById('cacheHits').textContent = data.cacheHits || 0;
            document.getElementById('memoryHistory').innerHTML = (data.history || []).map(h => 
                `<div style="padding: 0.5rem; background: #0f172a; margin-bottom: 0.5rem; border-radius: 0.25rem;">${h}</div>`
            ).join('') || '<p style="color: #94a3b8;">No history</p>';
        }
        
        function updateConfig(data) {
            const display = document.getElementById('configDisplay');
            display.innerHTML = Object.entries(data.config || {}).map(([k, v]) => 
                `<div style="padding: 0.5rem; border-bottom: 1px solid #334155;"><strong>${k}:</strong> ${JSON.stringify(v)}</div>`
            ).join('') || '<p style="color: #94a3b8;">No configuration</p>';
        }
        
        function updateLogs(data) {
            const output = document.getElementById('logOutput');
            output.innerHTML = (data.logs || []).map(l => 
                `<div class="log-entry ${l.level}">[${l.time}] ${l.message}</div>`
            ).join('') || '<div class="log-entry">No logs</div>';
        }
        
        async function removeAgent(name) {
            if (confirm('Remove agent ' + name + '?')) {
                await fetch('/api/agents/' + name, { method: 'DELETE' });
                loadTabData('agents');
            }
        }
        
        async function terminateSwarm(id) {
            if (confirm('Terminate swarm ' + id + '?')) {
                await fetch('/api/swarms/' + id, { method: 'POST' });
                loadTabData('swarms');
            }
        }
        
        setInterval(loadOverview, 30000);
        loadOverview();
    </script>
</body>
</html>
"""


@dataclass
class DashboardState:
    """Current state of the dashboard."""
    agent_registry: Dict[str, Any]
    swarm_stats: Dict[str, Any]
    cache_stats: Dict[str, Any]
    memory_stats: Dict[str, Any]


class MyClawDashboard:
    """Web Dashboard for MyClaw administration."""
    
    def __init__(
        self,
        agent_registry: Optional[Dict[str, Any]] = None,
        swarm_orchestrator=None,
        semantic_cache=None,
        memory=None,
        config=None,
        host: str = "127.0.0.1",
        port: int = 8080
    ):
        self._agent_registry = agent_registry or {}
        self._swarm_orchestrator = swarm_orchestrator
        self._semantic_cache = semantic_cache
        self._memory = memory
        self._config = config
        self._host = host
        self._port = port
        self._app: Optional[FastAPI] = None
        self._log_buffer: List[Dict[str, str]] = []
    
    async def start(self):
        """Start the dashboard server."""
        from myclaw.dashboard_server import create_dashboard_app
        
        self._app = create_dashboard_app(self)
        self._log("Dashboard started on " + self._host + ":" + str(self._port))
        
        import uvicorn
        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning"
        )
        server = uvicorn.Server(config)
        await server.serve()
    
    def _log(self, message: str, level: str = "info"):
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": message,
            "level": level
        }
        self._log_buffer.append(entry)
        if len(self._log_buffer) > 1000:
            self._log_buffer = self._log_buffer[-500:]
    
    def get_overview_data(self) -> Dict[str, Any]:
        """Get overview statistics."""
        return {
            "agents": len(self._agent_registry),
            "swarms": self._get_swarm_stats(),
            "cacheHitRate": self._get_cache_stats(),
            "memoryUsage": self._get_memory_usage(),
            "recentActivity": self._get_recent_activity()
        }
    
    def _get_swarm_stats(self) -> int:
        if self._swarm_orchestrator:
            stats = self._swarm_orchestrator.get_stats()
            return stats.get("running", 0)
        return 0
    
    def _get_cache_stats(self) -> int:
        if self._semantic_cache:
            return getattr(self._semantic_cache, 'hit_rate', 0) or 0
        return 0
    
    def _get_memory_usage(self) -> str:
        if self._memory:
            return "0MB"
        return "0MB"
    
    def _get_recent_activity(self) -> List[Dict[str, str]]:
        return []
    
    def get_agent_data(self) -> Dict[str, Any]:
        """Get agent data."""
        agents = [
            {
                "name": name,
                "type": getattr(agent, '__class__', 'Unknown').name,
                "status": "online"
            }
            for name, agent in self._agent_registry.items()
        ]
        return {"agents": agents}
    
    def get_swarm_data(self) -> Dict[str, Any]:
        """Get swarm data."""
        if not self._swarm_orchestrator:
            return {"swarms": []}
        
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
    
    def get_memory_data(self) -> Dict[str, Any]:
        """Get memory/cache data."""
        cache_hits = 0
        cache_size = 0
        
        if self._semantic_cache:
            stats = getattr(self._semantic_cache, 'stats', {}) or {}
            cache_hits = stats.get('hits', 0)
            cache_size = stats.get('size', 0)
        
        return {
            "cacheSize": cache_size,
            "cacheHits": cache_hits,
            "history": []
        }
    
    def get_config_data(self) -> Dict[str, Any]:
        """Get configuration data."""
        if not self._config:
            return {"config": {}}
        
        return {"config": {
            "provider": getattr(self._config, 'provider', 'auto'),
            "model": getattr(self._config, 'model', None),
            "temperature": getattr(self._config, 'temperature', 0.7),
            "max_tokens": getattr(self._config, 'max_tokens', 4096)
        }}
    
    def get_log_data(self) -> Dict[str, Any]:
        """Get log data."""
        return {"logs": self._log_buffer[-50:]}


@asynccontextmanager
async def dashboard_app(dashboard: "MyClawDashboard"):
    """Async context manager for managing the dashboard lifecycle."""
    from .dashboard_server import create_dashboard_app
    app = create_dashboard_app(dashboard)
    yield app


__all__ = [
    "MyClawDashboard",
    "create_dashboard_app",
    "DASHBOARD_HTML",
]