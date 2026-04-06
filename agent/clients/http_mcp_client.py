import logging
from contextlib import AsyncExitStack
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
        self._exit_stack = AsyncExitStack()
        logger.debug("HttpMCPClient instance created", extra={"server_url": mcp_server_url})

    @classmethod
    async def create(cls, mcp_server_url: str) -> 'HttpMCPClient':
        """Async factory method to create and connect MCPClient"""
        instance = cls(mcp_server_url)
        await instance.connect()
        return instance

    async def connect(self):
        """Connect to MCP server"""
        read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
            streamablehttp_client(self.server_url)
        )
        self.session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        init_result = await self.session.initialize()
        logger.info(f"MCP HTTP server initialized: {init_result}")

    async def close(self):
        """Close MCP connection"""
        await self._exit_stack.aclose()

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
