import json
import logging
import os
import uuid
from datetime import datetime, UTC
from typing import Optional, AsyncGenerator

import redis.asyncio as redis

from agent.clients.dial_client import DialClient
from agent.models.message import Message, Role
from agent.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

CONVERSATION_PREFIX = "conversation:"
CONVERSATION_LIST_KEY = "conversations:list"


class ConversationManager:
    """Manages conversation lifecycle including AI interactions and persistence"""

    def __init__(self, dial_client: DialClient, redis_client: redis.Redis):
        self.dial_client = dial_client
        self.redis = redis_client
        logger.info("ConversationManager initialized")

    async def create_conversation(self, title: str) -> dict:
        """Create a new conversation"""
        conversation_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        conversation = {
            "id": conversation_id,
            "title": title,
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        await self.redis.set(f"{CONVERSATION_PREFIX}{conversation_id}", json.dumps(conversation))
        await self.redis.zadd(CONVERSATION_LIST_KEY, {conversation_id: datetime.now(UTC).timestamp()})
        logger.info(f"Conversation created: id={conversation_id}, title={title}")
        return conversation

    async def list_conversations(self) -> list[dict]:
        """List all conversations sorted by last update time"""
        conversation_ids = await self.redis.zrevrange(CONVERSATION_LIST_KEY, 0, -1)
        conversations = []
        for conversation_id in conversation_ids:
            data = await self.redis.get(f"{CONVERSATION_PREFIX}{conversation_id}")
            if data:
                conv = json.loads(data)
                conversations.append({
                    "id": conv["id"],
                    "title": conv["title"],
                    "created_at": conv["created_at"],
                    "updated_at": conv["updated_at"],
                    "message_count": len(conv["messages"]),
                })
        return conversations

    async def get_conversation(self, conversation_id: str) -> Optional[dict]:
        """Get a specific conversation"""
        data = await self.redis.get(f"{CONVERSATION_PREFIX}{conversation_id}")
        if not data:
            return None
        return json.loads(data)

    async def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation"""
        deleted = await self.redis.delete(f"{CONVERSATION_PREFIX}{conversation_id}")
        return bool(deleted)

    async def chat(
            self,
            user_message: Message,
            conversation_id: str,
            stream: bool = False
    ):
        """
        Process chat messages and return AI response.
        Automatically saves conversation state.
        """
        logger.info(f"Processing chat request: conversation_id={conversation_id}, stream={stream}")
        conversation = await self.get_conversation(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation {conversation_id} not found")
        messages = [Message(**msg_data) for msg_data in conversation["messages"]]
        if not messages:
            messages.append(Message(role=Role.SYSTEM, content=SYSTEM_PROMPT))
        messages.append(user_message)
        if stream:
            return self._stream_chat(conversation_id, messages)
        return await self._non_stream_chat(conversation_id, messages)

    async def _stream_chat(
            self,
            conversation_id: str,
            messages: list[Message],
    ) -> AsyncGenerator[str, None]:
        """Handle streaming chat with automatic saving"""
        yield f"data: {json.dumps({'conversation_id': conversation_id})}\n\n"
        async for chunk in self.dial_client.stream_response(messages):
            yield chunk
        await self._save_conversation_messages(conversation_id, messages)

    async def _non_stream_chat(
            self,
            conversation_id: str,
            messages: list[Message],
    ) -> dict:
        """Handle non-streaming chat"""
        ai_message = await self.dial_client.response(messages)
        messages.append(ai_message)
        await self._save_conversation_messages(conversation_id, messages)
        return {
            "content": ai_message.content or "",
            "conversation_id": conversation_id,
        }

    async def _save_conversation_messages(
            self,
            conversation_id: str,
            messages: list[Message]
    ):
        """Save or update conversation messages"""
        data = await self.redis.get(f"{CONVERSATION_PREFIX}{conversation_id}")
        conversation = json.loads(data)
        conversation["messages"] = [msg.model_dump() for msg in messages]
        conversation["updated_at"] = datetime.now(UTC).isoformat()
        await self._save_conversation(conversation)

    async def _save_conversation(self, conversation: dict):
        """Internal method to persist conversation to Redis"""
        conversation_id = conversation["id"]
        await self.redis.set(f"{CONVERSATION_PREFIX}{conversation_id}", json.dumps(conversation))
        await self.redis.zadd(CONVERSATION_LIST_KEY, {conversation_id: datetime.now(UTC).timestamp()})
