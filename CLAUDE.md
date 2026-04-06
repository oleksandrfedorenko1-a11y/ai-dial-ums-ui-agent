# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A course exercise project (EPAM DIAL course) building a production-style Users Management Agent (UMS) — a conversational AI assistant that manages user records via natural language. Tool use is handled via MCP (Model Context Protocol) servers, conversation history is persisted in Redis, and a streaming chat UI (SSE) serves as the frontend.

## Running the Project

```bash
# Start infrastructure (Redis, UMS MCP server, mock user service, RedisInsight)
docker compose up -d

# Install dependencies
pip install -r requirements.txt

# Run the FastAPI server (port 8011)
python agent/app.py
# or: uv run python agent/app.py
```

There are no lint or test commands configured — the project has no test files and no test framework in `pyproject.toml`.

## Environment

- Python 3.13 required (`.python-version`)
- Config via `.env`: `DIAL_API_KEY` and `DIAL_ENDPOINT` (EPAM AI DIAL proxy at `https://ai-proxy.lab.epam.com`)
- Dependencies: `openai==2.0.0`, `fastmcp==2.10.1`, `redis[hiredis]==5.0.0`, `fastapi==0.118.0`, `httpx`

## Architecture

### Data Flow

1. Frontend (`index.html`) sends chat messages to `POST /api/conversations/{id}/chat`
2. `ConversationManager` loads history from Redis, prepends system prompt on first message
3. `DialClient` calls the DIAL proxy (via `AsyncAzureOpenAI`) with tool definitions injected
4. When the LLM returns `tool_calls`, `DialClient._call_tools()` dispatches each to the appropriate MCP client via `tool_name_client_map: dict[str, HttpMCPClient | StdioMCPClient]`
5. Tool results are appended and the LLM is called again recursively until no more tool calls remain
6. Final response is streamed back via SSE or returned as JSON

### Key Files

| File | Role |
|---|---|
| `agent/app.py` | FastAPI entry point, lifespan (MCP client init/close), all REST endpoints |
| `agent/conversation_manager.py` | Redis-backed conversation CRUD; routes chat through `DialClient` |
| `agent/clients/dial_client.py` | `AsyncAzureOpenAI` wrapper; streaming + non-streaming completions; recursive tool-call loop |
| `agent/clients/http_mcp_client.py` | Connects to HTTP MCP servers via `mcp.client.streamable_http` |
| `agent/clients/stdio_mcp_client.py` | Connects to Docker-based MCP servers via stdio |
| `agent/models/message.py` | Pydantic `Message` model with roles `system/user/assistant/tool`; supports `tool_calls` and `tool_call_id` |
| `agent/prompts.py` | `SYSTEM_PROMPT` constant |
| `index.html` | Single-file vanilla JS/HTML chat UI; markdown rendering via `marked.js` CDN |

### MCP Tool Servers

| Server | Transport | Endpoint |
|---|---|---|
| UMS MCP | HTTP | `http://localhost:8005/mcp` |
| Fetch MCP | HTTP | `https://remote.mcpservers.org/fetch/mcp` |
| DuckDuckGo | stdio/Docker | image `mcp/duckduckgo:latest` |

### Redis Schema

- `conversation:<id>` — hash storing message history
- `conversations:list` — sorted set for listing conversations

### MCP → OpenAI Tool Format Conversion

MCP tools use Anthropic's spec. They must be converted to OpenAI function-calling format before being passed to `DialClient`:
```json
{ "type": "function", "function": { "name": "...", "description": "...", "parameters": {...} } }
```

### Infrastructure Ports

| Service | Port |
|---|---|
| FastAPI agent | 8011 |
| Mock user service | 8041 |
| UMS MCP server | 8005 |
| Redis | 6379 |
| RedisInsight | 6380 |

## Current Implementation Status

All files except `agent/models/message.py` and `docker-compose.yml` contain `raise NotImplementedError()` stubs. The `index.html` JS functions `loadConversations`, `loadConversation`, `deleteConversation`, and `streamResponse` are also stubs.
