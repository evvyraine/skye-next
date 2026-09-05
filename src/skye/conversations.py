import asyncio
from collections import defaultdict

from .db import Database


class ConversationService:
    """Local-only conversation ids. History lives in DatabaseSession (SQLite)."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._locks: defaultdict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get_or_create(self, chat_id: int, thread_id: int) -> str:
        key = chat_id, thread_id
        async with self._locks[key]:
            existing = await self.database.conversation_id(*key)
            if existing:
                return existing
            conversation_id = f"telegram:{chat_id}:{thread_id}"
            await self.database.save_conversation(chat_id, thread_id, conversation_id)
            return conversation_id

    async def reset(self, chat_id: int, thread_id: int) -> bool:
        key = chat_id, thread_id
        async with self._locks[key]:
            conversation_id = await self.database.pop_conversation(*key)
            if not conversation_id:
                return False
            await self.database.clear_session(conversation_id)
            return True

    async def has_items(self, conversation_id: str) -> bool:
        return await self.database.session_has_items(conversation_id)
