from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, cast

import aiosqlite

from .config import ModelId, Reasoning
from .models import ChatSettings, Scope

AccessEffect = Literal["allow", "ban"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS access_entries (
    kind TEXT NOT NULL CHECK (kind IN ('user', 'chat')),
    telegram_id INTEGER NOT NULL,
    effect TEXT NOT NULL CHECK (effect IN ('allow', 'ban')),
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (kind, telegram_id)
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    chat_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL DEFAULT 0,
    openai_conversation_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, thread_id)
);

CREATE TABLE IF NOT EXISTS updates (
    update_id INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('processing', 'pending', 'done')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path, default_model: ModelId, default_reasoning: Reasoning) -> None:
        self.path = path
        self.default_model = default_model
        self.default_reasoning = default_reasoning
        self.connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA foreign_keys=ON")
        await self.connection.execute("PRAGMA busy_timeout=5000")
        await self.connection.executescript(SCHEMA)
        await self.connection.commit()

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not open")
        return self.connection

    async def _write(self, sql: str, parameters: Sequence[object] = ()) -> aiosqlite.Cursor:
        async with self._write_lock:
            cursor = await self.conn.execute(sql, parameters)
            await self.conn.commit()
            return cursor

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                await self.conn.rollback()
                raise
            else:
                await self.conn.commit()

    async def access_effect(self, scope: Scope) -> AccessEffect | None:
        cursor = await self.conn.execute(
            "SELECT effect FROM access_entries WHERE kind = ? AND telegram_id = ?",
            (scope.kind, scope.id),
        )
        row = await cursor.fetchone()
        return cast(AccessEffect, row["effect"]) if row else None

    async def set_access(self, scope: Scope, effect: AccessEffect, created_by: int) -> None:
        await self._write(
            """INSERT INTO access_entries (kind, telegram_id, effect, created_by)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(kind, telegram_id) DO UPDATE SET
                   effect = excluded.effect,
                   created_by = excluded.created_by,
                   created_at = CURRENT_TIMESTAMP""",
            (scope.kind, scope.id, effect, created_by),
        )

    async def remove_access(self, scope: Scope) -> bool:
        cursor = await self._write(
            "DELETE FROM access_entries WHERE kind = ? AND telegram_id = ?",
            (scope.kind, scope.id),
        )
        return cursor.rowcount > 0

    async def list_access(self) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT kind, telegram_id, effect, created_by, created_at "
            "FROM access_entries ORDER BY created_at"
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_settings(self, scope: Scope) -> ChatSettings:
        table, key = self._settings_table(scope)
        cursor = await self.conn.execute(
            f"SELECT model, reasoning FROM {table} WHERE {key} = ?",
            (scope.id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return ChatSettings(self.default_model, self.default_reasoning)
        return ChatSettings(cast(ModelId, row["model"]), cast(Reasoning, row["reasoning"]))

    async def set_model(self, scope: Scope, model: ModelId) -> ChatSettings:
        current = await self.get_settings(scope)
        result = ChatSettings(model, current.reasoning)
        await self._set_settings(scope, result)
        return result

    async def set_reasoning(self, scope: Scope, reasoning: Reasoning) -> ChatSettings:
        current = await self.get_settings(scope)
        result = ChatSettings(current.model, reasoning)
        await self._set_settings(scope, result)
        return result

    async def _set_settings(self, scope: Scope, settings: ChatSettings) -> None:
        table, key = self._settings_table(scope)
        await self._write(
            f"""INSERT INTO {table} ({key}, model, reasoning) VALUES (?, ?, ?)
                ON CONFLICT({key}) DO UPDATE SET
                    model = excluded.model,
                    reasoning = excluded.reasoning,
                    updated_at = CURRENT_TIMESTAMP""",
            (scope.id, settings.model, settings.reasoning),
        )

    @staticmethod
    def _settings_table(scope: Scope) -> tuple[str, str]:
        if scope.kind == "user":
            return "user_settings", "user_id"
        return "chat_settings", "chat_id"

    async def conversation_id(self, chat_id: int, thread_id: int) -> str | None:
        cursor = await self.conn.execute(
            "SELECT openai_conversation_id FROM conversations WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        )
        row = await cursor.fetchone()
        return cast(str, row["openai_conversation_id"]) if row else None

    async def save_conversation(self, chat_id: int, thread_id: int, conversation_id: str) -> None:
        await self._write(
            """INSERT INTO conversations (chat_id, thread_id, openai_conversation_id)
               VALUES (?, ?, ?)
               ON CONFLICT(chat_id, thread_id) DO UPDATE SET
                   openai_conversation_id = excluded.openai_conversation_id,
                   updated_at = CURRENT_TIMESTAMP""",
            (chat_id, thread_id, conversation_id),
        )

    async def pop_conversation(self, chat_id: int, thread_id: int) -> str | None:
        conversation_id = await self.conversation_id(chat_id, thread_id)
        await self._write(
            "DELETE FROM conversations WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        )
        return conversation_id

    async def claim_update(self, update_id: int, payload: str) -> bool:
        async with self.transaction() as connection:
            cursor = await connection.execute(
                "SELECT state FROM updates WHERE update_id = ?", (update_id,)
            )
            row = await cursor.fetchone()
            if row and row["state"] in {"done", "processing"}:
                return False
            await connection.execute(
                """INSERT INTO updates (update_id, payload, state, attempts)
                   VALUES (?, ?, 'processing', 1)
                   ON CONFLICT(update_id) DO UPDATE SET
                       payload = excluded.payload,
                       state = 'processing',
                       attempts = updates.attempts + 1,
                       last_error = NULL,
                       updated_at = CURRENT_TIMESTAMP""",
                (update_id, payload),
            )
        return True

    async def finish_update(self, update_id: int, error: str | None = None) -> None:
        await self._write(
            "UPDATE updates SET state = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE update_id = ?",
            ("pending" if error else "done", error, update_id),
        )

    async def pending_updates(self) -> list[str]:
        await self._write("UPDATE updates SET state = 'pending' WHERE state = 'processing'")
        cursor = await self.conn.execute(
            "SELECT payload FROM updates WHERE state = 'pending' ORDER BY update_id"
        )
        return [cast(str, row["payload"]) for row in await cursor.fetchall()]

    @staticmethod
    def encode_payload(payload: dict[str, Any]) -> str:
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
