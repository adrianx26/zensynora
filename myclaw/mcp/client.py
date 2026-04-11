import asyncio
import logging
from typing import Dict, Any, Callable, Awaitable
from inspect import Parameter, Signature

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import myclaw.tools as m_tools

logger = logging.getLogger(__name__)

class MCPClientManager:
    """Manages connections to external MCP servers and dynamic proxy tool registration."""
    def __init__(self, config: Dict[str, Any]):
        # server_name -> session object
        self.sessions: Dict[str, ClientSession] = {}
        # We need to keep references to the context managers to keep them alive
        self._contexts = []
        self.config = config.get("mcp", {}).get("servers", {})

    async def start_all(self):
        """Starts connections to all configured servers."""
        if not self.config:
            logger.info("No external MCP servers configured.")
            return

        for server_name, server_config in self.config.items():
            cmd = server_config.get("command")
            args = server_config.get("args", [])
            env = server_config.get("env", None)
            
            if not cmd:
                logger.warning(f"MCP Server '{server_name}' missing 'command'. Skipping.")
                continue
                
            logger.info(f"Connecting to MCP server '{server_name}': {cmd} {' '.join(args)}")
            asyncio.create_task(self._run_server(server_name, cmd, args, env))

    async def _run_server(self, name: str, cmd: str, args: list, env: dict = None):
        """Runs a single MCP server connection loop."""
        server_params = StdioServerParameters(command=cmd, args=args, env=env)
        
        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.sessions[name] = session
                    
                    logger.info(f"Successfully initialized MCP session with '{name}'")
                    await self._register_tools(name, session)
                    
                    # Keep the session alive indefinitely
                    while True:
                        await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"Failed to connect to MCP server '{name}': {e}")
            if name in self.sessions:
                del self.sessions[name]

    async def _register_tools(self, server_name: str, session: ClientSession):
        """Fetches tools from the server and registers them in ZenSynora."""
        try:
            tools_response = await session.list_tools()
            for t in tools_response.tools:
                # Add a prefix to ensure no collision with native tools
                local_tool_name = f"mcp_{server_name}_{t.name}"
                desc = t.description or "No description provided."
                
                # Create a proxy function that routes calls back to this session
                # Python late-binding requires us to capture these variables properly
                proxy_func = self._create_proxy(session, t.name)
                
                # Register in MyClaw's TOOL list
                m_tools.register_mcp_tool(t.name, server_name, proxy_func, desc)
                logger.debug(f"Registered external tool: mcp_{server_name}_{t.name}")
        except Exception as e:
            logger.error(f"Error fetching tools from '{server_name}': {e}")

    def _create_proxy(self, session: ClientSession, remote_tool_name: str) -> Callable[..., Awaitable[str]]:
        """Creates an async callable that proxies requests to the MCP server."""
        async def _proxy(**kwargs) -> str:
            # Reformat kwargs exactly into what MCP CallTool expects
            try:
                logger.debug(f"Calling MCP tool '{remote_tool_name}' with {kwargs}")
                response = await session.call_tool(remote_tool_name, arguments=kwargs)
                # MCP responses return 'content' which is a list of TextContent/ImageContent etc
                text_results = []
                for content in response.content:
                    if hasattr(content, 'type') and content.type == 'text':
                        text_results.append(content.text)
                    elif hasattr(content, 'text'):
                        text_results.append(content.text)
                    else:
                        text_results.append(str(content))
                
                if response.isError:
                    return f"Error from remote tool: {''.join(text_results)}"
                return "\n".join(text_results)
            except Exception as e:
                return f"Error executing MCP tool '{remote_tool_name}': {e}"
                
        # Hack to bypass signature inspection validation where some tools check params
        _proxy.__signature__ = Signature([Parameter("kwargs", Parameter.VAR_KEYWORD)]) 
        return _proxy
