import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

from agent.clients.dial_client import DialClient
from agent.clients.http_mcp_client import HttpMCPClient
from agent.clients.stdio_mcp_client import StdioMCPClient
from agent.conversation_manager import ConversationManager
from agent.models.message import Message

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

conversation_manager: Optional[ConversationManager] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize MCP clients, Redis, and ConversationManager on startup"""
    global conversation_manager

    logger.info("Application startup initiated")

    tools: list[dict] = []
    tool_name_client_map: dict[str, HttpMCPClient | StdioMCPClient] = {}
    stop_event = asyncio.Event()

    ums_mcp = await HttpMCPClient.create("http://localhost:8005/mcp", stop_event)
    for tool in await ums_mcp.get_tools():
        tools.append(tool)
        tool_name_client_map[tool["function"]["name"]] = ums_mcp

    fetch_mcp = await HttpMCPClient.create("https://remote.mcpservers.org/fetch/mcp", stop_event)
    for tool in await fetch_mcp.get_tools():
        tools.append(tool)
        tool_name_client_map[tool["function"]["name"]] = fetch_mcp

    duckduckgo_mcp = await StdioMCPClient.create("mcp/duckduckgo:latest", stop_event)
    for tool in await duckduckgo_mcp.get_tools():
        tools.append(tool)
        tool_name_client_map[tool["function"]["name"]] = duckduckgo_mcp

    dial_client = DialClient(
        api_key=os.getenv("DIAL_API_KEY"),
        endpoint="https://ai-proxy.lab.epam.com",
        model="gpt-4o",
        tools=tools,
        tool_name_client_map=tool_name_client_map,
    )

    redis_client = redis.Redis(host="localhost", port=6379, decode_responses=True)
    await redis_client.ping()
    logger.info("Redis connection verified")

    conversation_manager = ConversationManager(dial_client, redis_client)
    logger.info("Application startup complete")
    yield

    stop_event.set()
    await asyncio.gather(
        ums_mcp._task, fetch_mcp._task, duckduckgo_mcp._task,
        return_exceptions=True,
    )
    await redis_client.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request/Response Models
class ChatRequest(BaseModel):
    message: Message
    stream: bool = True


class ChatResponse(BaseModel):
    content: str
    conversation_id: str


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class CreateConversationRequest(BaseModel):
    title: str = None


# Endpoints
@app.get("/health")
async def health():
    """Health check endpoint"""
    logger.debug("Health check requested")
    return {
        "status": "healthy",
        "conversation_manager_initialized": conversation_manager is not None
    }


@app.post("/conversations")
async def create_conversation(request: CreateConversationRequest):
    return await conversation_manager.create_conversation(request.title or "New Conversation")


@app.get("/conversations")
async def list_conversations():
    return await conversation_manager.list_conversations()


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    conv = await conversation_manager.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    deleted = await conversation_manager.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"message": f"Conversation {conversation_id} has been deleted"}


@app.post("/conversations/{conversation_id}/chat")
async def chat(conversation_id: str, request: ChatRequest):
    try:
        result = await conversation_manager.chat(request.message, conversation_id, request.stream)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if request.stream:
        return StreamingResponse(result, media_type="text/event-stream")
    return ChatResponse(**result)


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting UMS Agent server")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8011,
        log_level="debug",
    )
