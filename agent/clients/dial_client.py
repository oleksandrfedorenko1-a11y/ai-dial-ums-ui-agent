import json
import logging
from collections import defaultdict
from typing import Any, AsyncGenerator

from openai import AsyncAzureOpenAI

from agent.clients.stdio_mcp_client import StdioMCPClient
from agent.models.message import Message, Role
from agent.clients.http_mcp_client import HttpMCPClient

logger = logging.getLogger(__name__)


class DialClient:
    """Handles AI model interactions and integrates with MCP client"""

    def __init__(
            self,
            api_key: str,
            endpoint: str,
            model: str,
            tools: list[dict[str, Any]],
            tool_name_client_map: dict[str, HttpMCPClient | StdioMCPClient]
    ):
        self.tools = tools
        self.tool_name_client_map = tool_name_client_map
        self.model = model
        self.async_openai = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version="",
        )

    async def response(self, messages: list[Message]) -> Message:
        """Non-streaming completion with tool calling support"""
        response = await self.async_openai.chat.completions.create(
            model=self.model,
            messages=[m.to_dict() for m in messages],
            tools=self.tools,
        )
        msg = response.choices[0].message
        ai_message = Message(
            role=Role.ASSISTANT,
            content=msg.content,
            tool_calls=[tc.model_dump() for tc in msg.tool_calls] if msg.tool_calls else None,
        )
        if ai_message.tool_calls:
            messages.append(ai_message)
            await self._call_tools(ai_message, messages)
            return await self.response(messages)
        return ai_message

    async def stream_response(self, messages: list[Message]) -> AsyncGenerator[str, None]:
        """
        Streaming completion with tool calling support.
        Yields SSE-formatted chunks.
        """
        stream = await self.async_openai.chat.completions.create(
            model=self.model,
            messages=[m.to_dict() for m in messages],
            tools=self.tools,
            stream=True,
        )
        content_buffer = ""
        tool_deltas = []

        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                chunk_data = {"choices": [{"delta": {"content": delta.content}, "index": 0, "finish_reason": None}]}
                yield f"data: {json.dumps(chunk_data)}\n\n"
                content_buffer += delta.content
            if delta.tool_calls:
                tool_deltas.extend(delta.tool_calls)

        if tool_deltas:
            tool_calls = self._collect_tool_calls(tool_deltas)
            ai_message = Message(role=Role.ASSISTANT, content=content_buffer or None, tool_calls=tool_calls)
            messages.append(ai_message)
            await self._call_tools(ai_message, messages)
            async for chunk in self.stream_response(messages):
                yield chunk
            return

        messages.append(Message(role=Role.ASSISTANT, content=content_buffer))
        final_chunk = {"choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]}
        yield f"data: {json.dumps(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    def _collect_tool_calls(self, tool_deltas):
        """Convert streaming tool call deltas to complete tool calls"""
        tool_dict = defaultdict(lambda: {"id": None, "function": {"arguments": "", "name": None}, "type": None})
        for delta in tool_deltas:
            idx = delta.index
            if delta.id:
                tool_dict[idx]["id"] = delta.id
            if delta.function.name:
                tool_dict[idx]["function"]["name"] = delta.function.name
            if delta.function.arguments:
                tool_dict[idx]["function"]["arguments"] += delta.function.arguments
            if delta.type:
                tool_dict[idx]["type"] = delta.type
        return list(tool_dict.values())

    async def _call_tools(self, ai_message: Message, messages: list[Message], silent: bool = False):
        """Execute tool calls using MCP client"""
        for tool_call in ai_message.tool_calls:
            tool_name = tool_call["function"]["name"]
            tool_args = json.loads(tool_call["function"]["arguments"])
            mcp_client = self.tool_name_client_map.get(tool_name)
            if not mcp_client:
                messages.append(Message(
                    role=Role.TOOL,
                    content=f"Tool '{tool_name}' not found",
                    tool_call_id=tool_call["id"],
                ))
                continue
            result = await mcp_client.call_tool(tool_name, tool_args)
            messages.append(Message(
                role=Role.TOOL,
                content=str(result),
                tool_call_id=tool_call["id"],
            ))
