import asyncio
import inspect
import logging

from mcp.server import Server
from mcp.types import Tool, TextContent
import myclaw.tools as m_tools

logger = logging.getLogger(__name__)


async def start_mcp_server():
    """Starts the MCP server over stdio, exposing MyClaw's native tools."""
    server = Server("zensynora")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools_list = []
        for name, data in m_tools.TOOLS.items():
            func = data["func"]
            desc = data.get("desc", "No description")

            sig = inspect.signature(func)
            properties = {}
            required = []

            for p_name, param in sig.parameters.items():
                if p_name in ["kwargs", "args", "self", "context"]:
                    continue
                # Default property type to basic string since python signatures lack strict runtime static types here
                properties[p_name] = {"type": "string"}
                if param.default == inspect.Parameter.empty:
                    required.append(p_name)

            input_schema = {"type": "object", "properties": properties, "required": required}

            tools_list.append(Tool(name=name, description=desc, inputSchema=input_schema))

        return tools_list

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name not in m_tools.TOOLS:
            raise ValueError(f"Tool {name} not found in ZenSynora registry.")

        func = m_tools.TOOLS[name]["func"]

        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**arguments)
            else:
                result = await asyncio.to_thread(func, **arguments)
            return [TextContent(type="text", text=str(result))]
        except Exception as e:
            # SECURITY FIX: Do not leak internal exception details to MCP clients.
            logger.error(f"MCP server execution error for '{name}': {e}", exc_info=True)
            return [
                TextContent(
                    type="text", text="Execution Error: Tool execution failed. Check server logs."
                )
            ]

    # Run the server loop indefinitely over stdin/stdout
    from mcp.server.stdio import stdio_server

    logger.info("Initializing MCP Stdio transport layer...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
