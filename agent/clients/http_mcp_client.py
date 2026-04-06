import asyncio
import logging
from typing import Optional, Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, TextContent

logger = logging.getLogger(__name__)


class HttpMCPClient:
    """Handles MCP server connection and tool execution"""

    def __init__(self, mcp_server_url: str) -> None:
        self.server_url = mcp_server_url
        self.session: Optional[ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        logger.debug("HttpMCPClient instance created", extra={"server_url": mcp_server_url})

    @classmethod
    async def create(cls, mcp_server_url: str, stop_event: asyncio.Event) -> 'HttpMCPClient':
        """Async factory method: starts a background task that owns the MCP connection lifetime"""
        instance = cls(mcp_server_url)
        ready = asyncio.Event()
        instance._task = asyncio.create_task(instance._run(ready, stop_event))
        await ready.wait()
        return instance

    async def _run(self, ready: asyncio.Event, stop: asyncio.Event) -> None:
        """Background task: owns the context managers for the full lifetime"""
        async with streamablehttp_client(self.server_url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                init_result = await session.initialize()
                logger.info(f"MCP HTTP server initialized: {init_result}")
                self.session = session
                ready.set()
                await stop.wait()
        self.session = None

    async def get_tools(self) -> list[dict[str, Any]]:
        """Get available tools from MCP server"""
        if not self.session:
            raise RuntimeError("MCP client is not connected to MCP server")
        tools_result = await self.session.list_tools()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                },
            }
            for tool in tools_result.tools
        ]
        logger.info(f"Retrieved tools from {self.server_url}: {[t['function']['name'] for t in tools]}")
        return tools

    async def call_tool(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        """Call a specific tool on the MCP server"""
        if not self.session:
            raise RuntimeError("MCP client is not connected to MCP server")
        logger.info(f"Calling MCP tool '{tool_name}' on {self.server_url} with args: {tool_args}")
        result: CallToolResult = await self.session.call_tool(tool_name, tool_args)
        content = result.content
        first_element = content[0]
        if isinstance(first_element, TextContent):
            return first_element.text
        return first_element
