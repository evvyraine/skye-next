import asyncio
from collections import defaultdict

import structlog
from openai import AsyncOpenAI

from .db import Database

log = structlog.get_logger()


class ConversationService:
    def __init__(self, database: Database, client: AsyncOpenAI) -> None:
        self.database = database
        self.client = client
        self._locks: defaultdict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get_or_create(self, chat_id: int, thread_id: int) -> str:
        key = chat_id, thread_id
        async with self._locks[key]:
            existing = await self.database.conversation_id(*key)
            if existing:
                return existing
            conversation = await self.client.conversations.create(
                metadata={"telegram_chat": str(chat_id), "telegram_thread": str(thread_id)}
            )
            await self.database.save_conversation(chat_id, thread_id, conversation.id)
            return conversation.id

    async def reset(self, chat_id: int, thread_id: int) -> bool:
        key = chat_id, thread_id
        async with self._locks[key]:
            conversation_id = await self.database.pop_conversation(*key)
            if not conversation_id:
                return False
            try:
                await self.client.conversations.delete(conversation_id)
            except Exception as error:
                log.warning(
                    "conversation_delete_failed",
                    conversation_id=conversation_id,
                    error=type(error).__name__,
                )
            return True

    async def has_items(self, conversation_id: str) -> bool:
        page = await self.client.conversations.items.list(
            conversation_id, limit=1, order="desc"
        )
        return bool(page.data)
