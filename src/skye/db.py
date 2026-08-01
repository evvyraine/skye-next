from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, cast

import aiosqlite

from .config import ModelId, Reasoning
from .models import (
    AgentCapability,
    AgentProfile,
    AgentVersion,
    AgentVisibility,
    ChatSettings,
    GroupMessage,
    InstalledAgent,
    Memory,
    MemoryCategory,
    Scope,
)

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
    memory_enabled INTEGER NOT NULL DEFAULT 1,
    active_agent_id TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    memory_enabled INTEGER NOT NULL DEFAULT 1,
    active_agent_id TEXT,
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

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('user', 'chat')),
    scope_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (scope_kind, scope_id, content)
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('private', 'unlisted', 'public')),
    current_version INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_versions (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    instructions TEXT NOT NULL,
    model TEXT,
    capabilities TEXT NOT NULL,
    checksum TEXT NOT NULL,
    share_token TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent_id, version)
);

CREATE TABLE IF NOT EXISTS agent_installs (
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('user', 'chat')),
    scope_id INTEGER NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    installed_by INTEGER NOT NULL,
    installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scope_kind, scope_id, agent_id),
    FOREIGN KEY (agent_id, version) REFERENCES agent_versions(agent_id, version)
);

CREATE INDEX IF NOT EXISTS agent_installs_scope
ON agent_installs(scope_kind, scope_id, enabled, installed_at);

CREATE TABLE IF NOT EXISTS group_messages (
    chat_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL DEFAULT 0,
    message_id INTEGER NOT NULL,
    sender_id INTEGER,
    sender_name TEXT NOT NULL,
    sender_username TEXT,
    text TEXT NOT NULL DEFAULT '',
    media_kind TEXT,
    media_file_id TEXT,
    reply_to_message_id INTEGER,
    reply_sender_name TEXT,
    reply_sender_username TEXT,
    reply_excerpt TEXT,
    sent_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS group_messages_context
ON group_messages(chat_id, thread_id, message_id DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    category,
    content='memories',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, category)
    VALUES (new.id, new.content, new.category);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, category)
    VALUES ('delete', old.id, old.content, old.category);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, category)
    VALUES ('delete', old.id, old.content, old.category);
    INSERT INTO memories_fts(rowid, content, category)
    VALUES (new.id, new.content, new.category);
END;
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
        await self._ensure_column("user_settings", "memory_enabled", "INTEGER NOT NULL DEFAULT 1")
        await self._ensure_column("chat_settings", "memory_enabled", "INTEGER NOT NULL DEFAULT 1")
        await self._ensure_column("user_settings", "active_agent_id", "TEXT")
        await self._ensure_column("chat_settings", "active_agent_id", "TEXT")
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

    async def _ensure_column(self, table: str, column: str, definition: str) -> None:
        cursor = await self.conn.execute(f"PRAGMA table_info({table})")
        if column not in {row[1] for row in await cursor.fetchall()}:
            await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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
            f"""SELECT model, reasoning, memory_enabled, active_agent_id
                FROM {table} WHERE {key} = ?""",
            (scope.id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return ChatSettings(self.default_model, self.default_reasoning)
        return ChatSettings(
            cast(ModelId, row["model"]),
            cast(Reasoning, row["reasoning"]),
            bool(row["memory_enabled"]),
            cast(str | None, row["active_agent_id"]),
        )

    async def set_model(self, scope: Scope, model: ModelId) -> ChatSettings:
        current = await self.get_settings(scope)
        result = ChatSettings(
            model, current.reasoning, current.memory_enabled, current.active_agent_id
        )
        await self._set_settings(scope, result)
        return result

    async def set_reasoning(self, scope: Scope, reasoning: Reasoning) -> ChatSettings:
        current = await self.get_settings(scope)
        result = ChatSettings(
            current.model, reasoning, current.memory_enabled, current.active_agent_id
        )
        await self._set_settings(scope, result)
        return result

    async def set_memory_enabled(self, scope: Scope, enabled: bool) -> ChatSettings:
        current = await self.get_settings(scope)
        result = ChatSettings(current.model, current.reasoning, enabled, current.active_agent_id)
        await self._set_settings(scope, result)
        return result

    async def set_active_agent(self, scope: Scope, agent_id: str | None) -> ChatSettings:
        current = await self.get_settings(scope)
        result = ChatSettings(current.model, current.reasoning, current.memory_enabled, agent_id)
        await self._set_settings(scope, result)
        return result

    async def _set_settings(self, scope: Scope, settings: ChatSettings) -> None:
        table, key = self._settings_table(scope)
        await self._write(
            f"""INSERT INTO {table} ({key}, model, reasoning, memory_enabled, active_agent_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT({key}) DO UPDATE SET
                    model = excluded.model,
                    reasoning = excluded.reasoning,
                    memory_enabled = excluded.memory_enabled,
                    active_agent_id = excluded.active_agent_id,
                    updated_at = CURRENT_TIMESTAMP""",
            (
                scope.id,
                settings.model,
                settings.reasoning,
                settings.memory_enabled,
                settings.active_agent_id,
            ),
        )

    @staticmethod
    def _settings_table(scope: Scope) -> tuple[str, str]:
        if scope.kind == "user":
            return "user_settings", "user_id"
        return "chat_settings", "chat_id"

    async def create_agent(
        self,
        *,
        agent_id: str,
        owner_id: int,
        scope: Scope,
        name: str,
        description: str,
        instructions: str,
        model: ModelId | None,
        capabilities: tuple[AgentCapability, ...],
        checksum: str,
    ) -> InstalledAgent:
        async with self.transaction() as connection:
            await connection.execute(
                """INSERT INTO agents (id, owner_id, visibility, current_version)
                   VALUES (?, ?, 'private', 1)""",
                (agent_id, owner_id),
            )
            await connection.execute(
                """INSERT INTO agent_versions (
                       agent_id, version, name, description, instructions, model,
                       capabilities, checksum
                   ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_id,
                    name,
                    description,
                    instructions,
                    model,
                    json.dumps(capabilities, separators=(",", ":")),
                    checksum,
                ),
            )
            await connection.execute(
                """INSERT INTO agent_installs (
                       scope_kind, scope_id, agent_id, version, installed_by
                   ) VALUES (?, ?, ?, 1, ?)""",
                (scope.kind, scope.id, agent_id, owner_id),
            )
            if scope != Scope("user", owner_id):
                await connection.execute(
                    """INSERT INTO agent_installs (
                           scope_kind, scope_id, agent_id, version, installed_by
                       ) VALUES ('user', ?, ?, 1, ?)""",
                    (owner_id, agent_id, owner_id),
                )
        installed = await self.installed_agent(scope, agent_id)
        if installed is None:
            raise RuntimeError("Agent was not created")
        return installed

    async def create_agent_version(
        self,
        *,
        agent_id: str,
        owner_id: int,
        scope: Scope,
        name: str,
        description: str,
        instructions: str,
        model: ModelId | None,
        capabilities: tuple[AgentCapability, ...],
        checksum: str,
    ) -> InstalledAgent:
        async with self.transaction() as connection:
            cursor = await connection.execute(
                "SELECT owner_id, current_version FROM agents WHERE id = ?", (agent_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                raise LookupError("Agent not found.")
            if row["owner_id"] != owner_id:
                raise PermissionError("Only the agent owner can edit it.")
            version = cast(int, row["current_version"]) + 1
            await connection.execute(
                """INSERT INTO agent_versions (
                       agent_id, version, name, description, instructions, model,
                       capabilities, checksum
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_id,
                    version,
                    name,
                    description,
                    instructions,
                    model,
                    json.dumps(capabilities, separators=(",", ":")),
                    checksum,
                ),
            )
            await connection.execute(
                """UPDATE agents SET current_version = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (version, agent_id),
            )
            await connection.execute(
                """INSERT INTO agent_installs (
                       scope_kind, scope_id, agent_id, version, installed_by
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(scope_kind, scope_id, agent_id) DO UPDATE SET
                       version = excluded.version,
                       enabled = 1,
                       installed_by = excluded.installed_by,
                       installed_at = CURRENT_TIMESTAMP""",
                (scope.kind, scope.id, agent_id, version, owner_id),
            )
            if scope != Scope("user", owner_id):
                await connection.execute(
                    """UPDATE agent_installs SET version = ?, enabled = 1,
                           installed_at = CURRENT_TIMESTAMP
                       WHERE scope_kind = 'user' AND scope_id = ? AND agent_id = ?""",
                    (version, owner_id, agent_id),
                )
        installed = await self.installed_agent(scope, agent_id)
        if installed is None:
            raise RuntimeError("Agent version was not saved")
        return installed

    async def agent_profile(self, agent_id: str) -> AgentProfile | None:
        cursor = await self.conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        return self._agent_profile(row) if row else None

    async def agent_version(self, agent_id: str, version: int) -> AgentVersion | None:
        cursor = await self.conn.execute(
            "SELECT * FROM agent_versions WHERE agent_id = ? AND version = ?",
            (agent_id, version),
        )
        row = await cursor.fetchone()
        return self._agent_version(row) if row else None

    async def installed_agent(self, scope: Scope, agent_id: str) -> InstalledAgent | None:
        cursor = await self.conn.execute(
            """SELECT
                   i.scope_kind, i.scope_id, i.enabled, i.installed_by, i.installed_at,
                   a.id, a.owner_id, a.visibility, a.current_version, a.created_at,
                   a.updated_at, v.version, v.name, v.description, v.instructions,
                   v.model, v.capabilities, v.checksum, v.share_token,
                   v.created_at AS version_created_at
               FROM agent_installs AS i
               JOIN agents AS a ON a.id = i.agent_id
               JOIN agent_versions AS v ON v.agent_id = i.agent_id AND v.version = i.version
               WHERE i.scope_kind = ? AND i.scope_id = ? AND i.agent_id = ?""",
            (scope.kind, scope.id, agent_id),
        )
        row = await cursor.fetchone()
        return self._installed_agent(row) if row else None

    async def installed_agents(
        self, scope: Scope, *, enabled_only: bool = False
    ) -> list[InstalledAgent]:
        enabled = " AND i.enabled = 1" if enabled_only else ""
        cursor = await self.conn.execute(
            """SELECT
                   i.scope_kind, i.scope_id, i.enabled, i.installed_by, i.installed_at,
                   a.id, a.owner_id, a.visibility, a.current_version, a.created_at,
                   a.updated_at, v.version, v.name, v.description, v.instructions,
                   v.model, v.capabilities, v.checksum, v.share_token,
                   v.created_at AS version_created_at
               FROM agent_installs AS i
               JOIN agents AS a ON a.id = i.agent_id
               JOIN agent_versions AS v ON v.agent_id = i.agent_id AND v.version = i.version
               WHERE i.scope_kind = ? AND i.scope_id = ?"""
            + enabled
            + " ORDER BY lower(v.name), i.installed_at",
            (scope.kind, scope.id),
        )
        return [self._installed_agent(row) for row in await cursor.fetchall()]

    async def share_agent_version(
        self, agent_id: str, owner_id: int, version: int, token: str
    ) -> str:
        async with self.transaction() as connection:
            cursor = await connection.execute(
                """SELECT a.owner_id, v.share_token
                   FROM agents AS a JOIN agent_versions AS v ON v.agent_id = a.id
                   WHERE a.id = ? AND v.version = ?""",
                (agent_id, version),
            )
            row = await cursor.fetchone()
            if row is None:
                raise LookupError("Agent version not found.")
            if row["owner_id"] != owner_id:
                raise PermissionError("Only the agent owner can share it.")
            existing = cast(str | None, row["share_token"])
            if existing:
                return existing
            await connection.execute(
                "UPDATE agent_versions SET share_token = ? WHERE agent_id = ? AND version = ?",
                (token, agent_id, version),
            )
            await connection.execute(
                """UPDATE agents SET visibility = 'unlisted', updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND visibility = 'private'""",
                (agent_id,),
            )
        return token

    async def shared_agent(self, token: str) -> tuple[AgentProfile, AgentVersion] | None:
        cursor = await self.conn.execute(
            """SELECT
                   a.id, a.owner_id, a.visibility, a.current_version, a.created_at,
                   a.updated_at, v.version, v.name, v.description, v.instructions,
                   v.model, v.capabilities, v.checksum, v.share_token,
                   v.created_at AS version_created_at
               FROM agent_versions AS v JOIN agents AS a ON a.id = v.agent_id
               WHERE v.share_token = ? AND a.visibility IN ('unlisted', 'public')""",
            (token,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._agent_profile(row), self._joined_agent_version(row)

    async def install_agent(
        self, scope: Scope, agent_id: str, version: int, installed_by: int
    ) -> InstalledAgent:
        await self._write(
            """INSERT INTO agent_installs (
                   scope_kind, scope_id, agent_id, version, installed_by
               ) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(scope_kind, scope_id, agent_id) DO UPDATE SET
                   version = excluded.version,
                   enabled = 1,
                   installed_by = excluded.installed_by,
                   installed_at = CURRENT_TIMESTAMP""",
            (scope.kind, scope.id, agent_id, version, installed_by),
        )
        installed = await self.installed_agent(scope, agent_id)
        if installed is None:
            raise RuntimeError("Agent was not installed")
        return installed

    async def remove_agent_install(self, scope: Scope, agent_id: str) -> bool:
        async with self.transaction() as connection:
            cursor = await connection.execute(
                """DELETE FROM agent_installs
                   WHERE scope_kind = ? AND scope_id = ? AND agent_id = ?""",
                (scope.kind, scope.id, agent_id),
            )
            table, key = self._settings_table(scope)
            await connection.execute(
                f"""UPDATE {table} SET active_agent_id = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE {key} = ? AND active_agent_id = ?""",
                (scope.id, agent_id),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _agent_profile(row: aiosqlite.Row) -> AgentProfile:
        return AgentProfile(
            id=cast(str, row["id"]),
            owner_id=cast(int, row["owner_id"]),
            visibility=cast(AgentVisibility, row["visibility"]),
            current_version=cast(int, row["current_version"]),
            created_at=cast(str, row["created_at"]),
            updated_at=cast(str, row["updated_at"]),
        )

    @staticmethod
    def _agent_version(row: aiosqlite.Row) -> AgentVersion:
        return AgentVersion(
            agent_id=cast(str, row["agent_id"]),
            version=cast(int, row["version"]),
            name=cast(str, row["name"]),
            description=cast(str, row["description"]),
            instructions=cast(str, row["instructions"]),
            model=cast(ModelId | None, row["model"]),
            capabilities=tuple(cast(list[AgentCapability], json.loads(row["capabilities"]))),
            checksum=cast(str, row["checksum"]),
            share_token=cast(str | None, row["share_token"]),
            created_at=cast(str, row["created_at"]),
        )

    @classmethod
    def _joined_agent_version(cls, row: aiosqlite.Row) -> AgentVersion:
        return AgentVersion(
            agent_id=cast(str, row["id"]),
            version=cast(int, row["version"]),
            name=cast(str, row["name"]),
            description=cast(str, row["description"]),
            instructions=cast(str, row["instructions"]),
            model=cast(ModelId | None, row["model"]),
            capabilities=tuple(cast(list[AgentCapability], json.loads(row["capabilities"]))),
            checksum=cast(str, row["checksum"]),
            share_token=cast(str | None, row["share_token"]),
            created_at=cast(str, row["version_created_at"]),
        )

    @classmethod
    def _installed_agent(cls, row: aiosqlite.Row) -> InstalledAgent:
        return InstalledAgent(
            scope=Scope(cast(Any, row["scope_kind"]), cast(int, row["scope_id"])),
            profile=cls._agent_profile(row),
            version=cls._joined_agent_version(row),
            enabled=bool(row["enabled"]),
            installed_by=cast(int, row["installed_by"]),
            installed_at=cast(str, row["installed_at"]),
        )

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

    async def save_group_message(self, message: GroupMessage) -> None:
        await self._write(
            """INSERT INTO group_messages (
                   chat_id, thread_id, message_id, sender_id, sender_name, sender_username,
                   text, media_kind, media_file_id, reply_to_message_id, reply_sender_name,
                   reply_sender_username, reply_excerpt, sent_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, message_id) DO UPDATE SET
                   thread_id = excluded.thread_id,
                   sender_id = excluded.sender_id,
                   sender_name = excluded.sender_name,
                   sender_username = excluded.sender_username,
                   text = excluded.text,
                   media_kind = excluded.media_kind,
                   media_file_id = excluded.media_file_id,
                   reply_to_message_id = excluded.reply_to_message_id,
                   reply_sender_name = excluded.reply_sender_name,
                   reply_sender_username = excluded.reply_sender_username,
                   reply_excerpt = excluded.reply_excerpt,
                   sent_at = excluded.sent_at""",
            (
                message.chat_id,
                message.thread_id,
                message.message_id,
                message.sender_id,
                message.sender_name,
                message.sender_username,
                message.text,
                message.media_kind,
                message.media_file_id,
                message.reply_to_message_id,
                message.reply_sender_name,
                message.reply_sender_username,
                message.reply_excerpt,
                message.sent_at,
            ),
        )

    async def prune_group_messages(self, chat_id: int, thread_id: int, keep: int) -> None:
        await self._write(
            """DELETE FROM group_messages
               WHERE chat_id = ? AND thread_id = ? AND message_id NOT IN (
                   SELECT message_id FROM group_messages
                   WHERE chat_id = ? AND thread_id = ?
                   ORDER BY message_id DESC LIMIT ?
               )""",
            (chat_id, thread_id, chat_id, thread_id, keep),
        )

    async def group_messages(
        self,
        chat_id: int,
        thread_id: int,
        *,
        before: int,
        limit: int,
    ) -> list[GroupMessage]:
        cursor = await self.conn.execute(
            """SELECT * FROM group_messages
               WHERE chat_id = ? AND thread_id = ? AND message_id < ?
               ORDER BY message_id DESC LIMIT ?""",
            (chat_id, thread_id, before, limit),
        )
        rows = list(await cursor.fetchall())
        return [self._group_message(row) for row in reversed(rows)]

    @staticmethod
    def _group_message(row: aiosqlite.Row) -> GroupMessage:
        return GroupMessage(
            chat_id=cast(int, row["chat_id"]),
            thread_id=cast(int, row["thread_id"]),
            message_id=cast(int, row["message_id"]),
            sender_id=cast(int | None, row["sender_id"]),
            sender_name=cast(str, row["sender_name"]),
            sender_username=cast(str | None, row["sender_username"]),
            text=cast(str, row["text"]),
            media_kind=cast(str | None, row["media_kind"]),
            media_file_id=cast(str | None, row["media_file_id"]),
            reply_to_message_id=cast(int | None, row["reply_to_message_id"]),
            reply_sender_name=cast(str | None, row["reply_sender_name"]),
            reply_sender_username=cast(str | None, row["reply_sender_username"]),
            reply_excerpt=cast(str | None, row["reply_excerpt"]),
            sent_at=cast(int, row["sent_at"]),
        )

    async def remember(
        self, scope: Scope, content: str, category: MemoryCategory
    ) -> Memory:
        await self._write(
            """INSERT INTO memories (scope_kind, scope_id, category, content)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(scope_kind, scope_id, content) DO UPDATE SET
                   category = excluded.category,
                   updated_at = CURRENT_TIMESTAMP""",
            (scope.kind, scope.id, category, content),
        )
        cursor = await self.conn.execute(
            """SELECT * FROM memories
               WHERE scope_kind = ? AND scope_id = ? AND content = ?""",
            (scope.kind, scope.id, content),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Memory was not saved")
        return self._memory(row)

    async def memories(self, scope: Scope, limit: int = 20) -> list[Memory]:
        cursor = await self.conn.execute(
            """SELECT * FROM memories WHERE scope_kind = ? AND scope_id = ?
               ORDER BY updated_at DESC, id DESC LIMIT ?""",
            (scope.kind, scope.id, limit),
        )
        return [self._memory(row) for row in await cursor.fetchall()]

    async def search_memories(
        self, scope: Scope, query: str, limit: int = 8
    ) -> list[Memory]:
        terms = re.findall(r"\w+", query.casefold(), flags=re.UNICODE)[:12]
        if not terms:
            return await self.memories(scope, limit)
        match = " OR ".join(f'"{term}"' for term in terms)
        cursor = await self.conn.execute(
            """SELECT m.* FROM memories_fts
               JOIN memories AS m ON m.id = memories_fts.rowid
               WHERE memories_fts MATCH ? AND m.scope_kind = ? AND m.scope_id = ?
               ORDER BY bm25(memories_fts), m.updated_at DESC LIMIT ?""",
            (match, scope.kind, scope.id, limit),
        )
        return [self._memory(row) for row in await cursor.fetchall()]

    async def forget_memory(self, scope: Scope, memory_id: int) -> bool:
        cursor = await self._write(
            "DELETE FROM memories WHERE id = ? AND scope_kind = ? AND scope_id = ?",
            (memory_id, scope.kind, scope.id),
        )
        return cursor.rowcount > 0

    async def clear_memories(self, scope: Scope) -> int:
        cursor = await self._write(
            "DELETE FROM memories WHERE scope_kind = ? AND scope_id = ?",
            (scope.kind, scope.id),
        )
        return cursor.rowcount

    @staticmethod
    def _memory(row: aiosqlite.Row) -> Memory:
        return Memory(
            id=cast(int, row["id"]),
            scope=Scope(cast(Any, row["scope_kind"]), cast(int, row["scope_id"])),
            category=cast(MemoryCategory, row["category"]),
            content=cast(str, row["content"]),
            created_at=cast(str, row["created_at"]),
            updated_at=cast(str, row["updated_at"]),
        )

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
