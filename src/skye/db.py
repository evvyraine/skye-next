from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import aiosqlite

from .config import ModelId, Reasoning
from .models import (
    AccessEffect,
    AccessEntry,
    AgentCapability,
    AgentProfile,
    AgentVersion,
    AgentVisibility,
    ChatSettings,
    ConnectorKind,
    ConnectorShare,
    CustomConnector,
    GroupMessage,
    InstalledAgent,
    KnownGroup,
    MediaGroupItem,
    Memory,
    MemoryCategory,
    PlanId,
    ProjectKind,
    Scope,
    ScopeKind,
    Skill,
    StarEntitlement,
    TelegramProject,
    ToolStatus,
    WebFile,
    WebFileKind,
    WebMessage,
    WebMessageRole,
    WebProject,
    WebSession,
)

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
    active_telegram_project_id TEXT,
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
    context_message_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, thread_id)
);

CREATE TABLE IF NOT EXISTS telegram_projects (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('skye', 'custom')),
    name TEXT NOT NULL,
    emoji TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    openai_conversation_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS telegram_projects_user
ON telegram_projects(user_id, updated_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS telegram_projects_skye
ON telegram_projects(user_id) WHERE kind = 'skye';

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

CREATE TABLE IF NOT EXISTS media_group_items (
    chat_id INTEGER NOT NULL,
    media_group_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL DEFAULT 0,
    media_kind TEXT NOT NULL,
    file_id TEXT NOT NULL,
    file_unique_id TEXT NOT NULL,
    file_name TEXT,
    mime_type TEXT,
    file_size INTEGER,
    width INTEGER,
    height INTEGER,
    caption TEXT,
    sent_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS media_group_items_lookup
ON media_group_items(chat_id, media_group_id, message_id);

CREATE TABLE IF NOT EXISTS media_group_claims (
    chat_id INTEGER NOT NULL,
    media_group_id TEXT NOT NULL,
    claimed_message_id INTEGER NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, media_group_id)
);

CREATE TABLE IF NOT EXISTS custom_connectors (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    headers TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS custom_connectors_user
ON custom_connectors(user_id, enabled, updated_at);

CREATE TABLE IF NOT EXISTS user_toolkits (
    user_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    PRIMARY KEY (user_id, slug)
);

CREATE TABLE IF NOT EXISTS composio_sessions (
    user_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    mcp_url TEXT NOT NULL,
    toolkit_key TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS composio_session_cache (
    user_id INTEGER NOT NULL,
    toolkit_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    mcp_url TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, toolkit_key)
);

CREATE TABLE IF NOT EXISTS known_chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS connector_shares (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    owner_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('app', 'custom')),
    ref TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chat_id, owner_id, kind, ref)
);

CREATE INDEX IF NOT EXISTS connector_shares_chat
ON connector_shares(chat_id, created_at);

CREATE INDEX IF NOT EXISTS connector_shares_owner
ON connector_shares(owner_id, kind, ref);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('user', 'chat')),
    scope_id INTEGER NOT NULL,
    openai_skill_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    filename TEXT NOT NULL,
    archive BLOB NOT NULL,
    file_count INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (scope_kind, scope_id, name)
);

CREATE INDEX IF NOT EXISTS skills_scope
ON skills(scope_kind, scope_id, created_at);

CREATE TABLE IF NOT EXISTS web_sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    username TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS web_sessions_user ON web_sessions(user_id, expires_at);

CREATE TABLE IF NOT EXISTS web_projects (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('skye', 'custom')),
    name TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL,
    color TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    openai_conversation_id TEXT,
    last_message_preview TEXT NOT NULL DEFAULT '',
    last_message_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS web_projects_user
ON web_projects(user_id, pinned DESC, last_message_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS web_projects_skye
ON web_projects(user_id) WHERE kind = 'skye';

CREATE TABLE IF NOT EXISTS web_messages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES web_projects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool', 'system')),
    text TEXT NOT NULL DEFAULT '',
    tool_name TEXT,
    tool_status TEXT,
    file_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS web_messages_project
ON web_messages(project_id, created_at, id);

CREATE TABLE IF NOT EXISTS web_files (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    project_id TEXT NOT NULL REFERENCES web_projects(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('upload', 'image', 'document')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS web_files_project ON web_files(project_id, created_at);

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

CREATE VIRTUAL TABLE IF NOT EXISTS web_messages_fts USING fts5(
    text,
    content='web_messages',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS web_messages_ai AFTER INSERT ON web_messages BEGIN
    INSERT INTO web_messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS web_messages_ad AFTER DELETE ON web_messages BEGIN
    INSERT INTO web_messages_fts(web_messages_fts, rowid, text)
    VALUES ('delete', old.rowid, old.text);
END;

CREATE TRIGGER IF NOT EXISTS web_messages_au AFTER UPDATE ON web_messages BEGIN
    INSERT INTO web_messages_fts(web_messages_fts, rowid, text)
    VALUES ('delete', old.rowid, old.text);
    INSERT INTO web_messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TABLE IF NOT EXISTS star_entitlements (
    user_id INTEGER PRIMARY KEY,
    plan TEXT NOT NULL CHECK (plan IN ('trial', 'plus', 'super', 'ultra')),
    auto_renew INTEGER NOT NULL DEFAULT 0,
    expires_at INTEGER NOT NULL,
    telegram_payment_charge_id TEXT,
    trial_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS star_payments (
    telegram_payment_charge_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    plan TEXT NOT NULL CHECK (plan IN ('trial', 'plus', 'super', 'ultra')),
    stars INTEGER NOT NULL,
    invoice_payload TEXT NOT NULL,
    is_recurring INTEGER NOT NULL DEFAULT 0,
    is_first_recurring INTEGER NOT NULL DEFAULT 0,
    subscription_expiration_date INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        await self._ensure_column("user_settings", "memory_enabled", "INTEGER NOT NULL DEFAULT 1")
        await self._ensure_column("chat_settings", "memory_enabled", "INTEGER NOT NULL DEFAULT 1")
        await self._ensure_column("user_settings", "active_agent_id", "TEXT")
        await self._ensure_column("chat_settings", "active_agent_id", "TEXT")
        await self._ensure_column(
            "composio_session_cache", "mcp_headers", "TEXT NOT NULL DEFAULT '{}'"
        )
        await self._ensure_column(
            "conversations", "context_message_id", "INTEGER NOT NULL DEFAULT 0"
        )
        await self._ensure_column("user_settings", "active_telegram_project_id", "TEXT")
        await self._normalize_group_message_threads()
        await self._migrate_composio_sessions()
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

    async def _migrate_composio_sessions(self) -> None:
        cursor = await self.conn.execute("SELECT COUNT(*) FROM composio_session_cache")
        row = await cursor.fetchone()
        if row and int(row[0]) > 0:
            return
        cursor = await self.conn.execute(
            "SELECT user_id, session_id, mcp_url, toolkit_key FROM composio_sessions"
        )
        for item in await cursor.fetchall():
            await self.conn.execute(
                """INSERT OR IGNORE INTO composio_session_cache
                   (user_id, toolkit_key, session_id, mcp_url)
                   VALUES (?, ?, ?, ?)""",
                (item["user_id"], item["toolkit_key"], item["session_id"], item["mcp_url"]),
            )

    async def _normalize_group_message_threads(self) -> None:
        await self.conn.execute(
            """UPDATE group_messages AS message
               SET thread_id = 0
               WHERE thread_id != 0
                 AND EXISTS (
                     SELECT 1 FROM updates
                     WHERE COALESCE(
                               json_extract(payload, '$.message.chat.id'),
                               json_extract(payload, '$.edited_message.chat.id')
                           ) = message.chat_id
                       AND COALESCE(
                               json_extract(payload, '$.message.message_id'),
                               json_extract(payload, '$.edited_message.message_id')
                           ) = message.message_id
                       AND COALESCE(
                               json_extract(payload, '$.message.is_topic_message'),
                               json_extract(payload, '$.edited_message.is_topic_message'),
                               0
                           ) = 0
                 )"""
        )

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

    async def list_access(self) -> list[AccessEntry]:
        cursor = await self.conn.execute(
            "SELECT kind, telegram_id, effect, created_by, created_at "
            "FROM access_entries ORDER BY created_at"
        )
        return [
            AccessEntry(
                Scope(cast(ScopeKind, row["kind"]), int(row["telegram_id"])),
                cast(AccessEffect, row["effect"]),
                int(row["created_by"]),
                str(row["created_at"]),
            )
            for row in await cursor.fetchall()
        ]

    async def star_entitlement(self, user_id: int) -> StarEntitlement | None:
        cursor = await self.conn.execute(
            """SELECT user_id, plan, auto_renew, expires_at, telegram_payment_charge_id,
                      trial_used, created_at, updated_at
               FROM star_entitlements WHERE user_id = ?""",
            (user_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else self._star_entitlement(row)

    async def active_entitlement(
        self, user_id: int, *, now: int | None = None
    ) -> StarEntitlement | None:
        current = await self.star_entitlement(user_id)
        if current is None or not current.active(int(time.time()) if now is None else now):
            return None
        return current

    async def star_trial_used(self, user_id: int) -> bool:
        current = await self.star_entitlement(user_id)
        return bool(current and current.trial_used)

    async def record_star_payment(
        self,
        *,
        telegram_payment_charge_id: str,
        user_id: int,
        plan: PlanId,
        stars: int,
        invoice_payload: str,
        is_recurring: bool,
        is_first_recurring: bool,
        subscription_expiration_date: int | None,
    ) -> bool:
        cursor = await self._write(
            """INSERT OR IGNORE INTO star_payments (
                   telegram_payment_charge_id, user_id, plan, stars, invoice_payload,
                   is_recurring, is_first_recurring, subscription_expiration_date
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                telegram_payment_charge_id,
                user_id,
                plan,
                stars,
                invoice_payload,
                int(is_recurring),
                int(is_first_recurring),
                subscription_expiration_date,
            ),
        )
        return cursor.rowcount > 0

    async def star_payment(self, telegram_payment_charge_id: str) -> tuple[int, PlanId] | None:
        cursor = await self.conn.execute(
            "SELECT user_id, plan FROM star_payments WHERE telegram_payment_charge_id = ?",
            (telegram_payment_charge_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return int(row["user_id"]), cast(PlanId, row["plan"])

    async def upsert_star_entitlement(
        self,
        *,
        user_id: int,
        plan: PlanId,
        auto_renew: bool,
        expires_at: int,
        telegram_payment_charge_id: str | None,
        trial_used: bool,
    ) -> StarEntitlement:
        await self._write(
            """INSERT INTO star_entitlements (
                   user_id, plan, auto_renew, expires_at, telegram_payment_charge_id, trial_used
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   plan = excluded.plan,
                   auto_renew = excluded.auto_renew,
                   expires_at = excluded.expires_at,
                   telegram_payment_charge_id = excluded.telegram_payment_charge_id,
                   trial_used = MAX(star_entitlements.trial_used, excluded.trial_used),
                   updated_at = CURRENT_TIMESTAMP""",
            (
                user_id,
                plan,
                int(auto_renew),
                expires_at,
                telegram_payment_charge_id,
                int(trial_used),
            ),
        )
        current = await self.star_entitlement(user_id)
        if current is None:
            raise RuntimeError("Star entitlement was not saved.")
        return current

    async def extend_star_entitlement(self, user_id: int, expires_at: int) -> StarEntitlement:
        await self._write(
            """UPDATE star_entitlements
               SET expires_at = ?, updated_at = CURRENT_TIMESTAMP
               WHERE user_id = ?""",
            (expires_at, user_id),
        )
        current = await self.star_entitlement(user_id)
        if current is None:
            raise RuntimeError("Star entitlement was not saved.")
        return current

    async def set_star_auto_renew(self, user_id: int, auto_renew: bool) -> StarEntitlement:
        await self._write(
            """UPDATE star_entitlements
               SET auto_renew = ?, updated_at = CURRENT_TIMESTAMP
               WHERE user_id = ?""",
            (int(auto_renew), user_id),
        )
        current = await self.star_entitlement(user_id)
        if current is None:
            raise RuntimeError("Star entitlement was not saved.")
        return current

    async def expire_star_entitlement(self, user_id: int, now: int) -> StarEntitlement | None:
        await self._write(
            """UPDATE star_entitlements
               SET auto_renew = 0, expires_at = ?, updated_at = CURRENT_TIMESTAMP
               WHERE user_id = ?""",
            (now, user_id),
        )
        return await self.star_entitlement(user_id)

    @staticmethod
    def _star_entitlement(row: aiosqlite.Row) -> StarEntitlement:
        return StarEntitlement(
            user_id=int(row["user_id"]),
            plan=cast(PlanId, row["plan"]),
            auto_renew=bool(row["auto_renew"]),
            expires_at=int(row["expires_at"]),
            telegram_payment_charge_id=cast(str | None, row["telegram_payment_charge_id"]),
            trial_used=bool(row["trial_used"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

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

    async def conversation_context_message_id(self, chat_id: int, thread_id: int) -> int:
        cursor = await self.conn.execute(
            "SELECT context_message_id FROM conversations WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        )
        row = await cursor.fetchone()
        return cast(int, row["context_message_id"]) if row else 0

    async def set_conversation_context_message_id(
        self, chat_id: int, thread_id: int, message_id: int
    ) -> None:
        await self._write(
            """UPDATE conversations
               SET context_message_id = ?, updated_at = CURRENT_TIMESTAMP
               WHERE chat_id = ? AND thread_id = ?""",
            (message_id, chat_id, thread_id),
        )

    async def pop_conversation(self, chat_id: int, thread_id: int) -> str | None:
        conversation_id = await self.conversation_id(chat_id, thread_id)
        await self._write(
            "DELETE FROM conversations WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        )
        return conversation_id

    async def active_telegram_project_id(self, user_id: int) -> str | None:
        cursor = await self.conn.execute(
            "SELECT active_telegram_project_id FROM user_settings WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return cast(str | None, row["active_telegram_project_id"])

    async def set_active_telegram_project_id(self, user_id: int, project_id: str) -> None:
        await self._write(
            """INSERT INTO user_settings (
                   user_id, model, reasoning, memory_enabled, active_telegram_project_id
               ) VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   active_telegram_project_id = excluded.active_telegram_project_id,
                   updated_at = CURRENT_TIMESTAMP""",
            (user_id, self.default_model, self.default_reasoning, project_id),
        )

    async def create_telegram_project(self, project: TelegramProject) -> TelegramProject:
        await self._write(
            """INSERT INTO telegram_projects (
                   id, user_id, kind, name, emoji, instructions, openai_conversation_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project.id,
                project.user_id,
                project.kind,
                project.name,
                project.emoji,
                project.instructions,
                project.openai_conversation_id,
            ),
        )
        saved = await self.telegram_project(project.user_id, project.id)
        if saved is None:
            raise RuntimeError("Project was not created")
        return saved

    async def telegram_project(self, user_id: int, project_id: str) -> TelegramProject | None:
        cursor = await self.conn.execute(
            "SELECT * FROM telegram_projects WHERE id = ? AND user_id = ?",
            (project_id, user_id),
        )
        row = await cursor.fetchone()
        return self._telegram_project(row) if row else None

    async def skye_telegram_project(self, user_id: int) -> TelegramProject | None:
        cursor = await self.conn.execute(
            "SELECT * FROM telegram_projects WHERE user_id = ? AND kind = 'skye'",
            (user_id,),
        )
        row = await cursor.fetchone()
        return self._telegram_project(row) if row else None

    async def list_telegram_projects(self, user_id: int) -> list[TelegramProject]:
        cursor = await self.conn.execute(
            """SELECT * FROM telegram_projects WHERE user_id = ?
               ORDER BY CASE WHEN kind = 'skye' THEN 0 ELSE 1 END,
                        updated_at DESC, created_at DESC""",
            (user_id,),
        )
        return [self._telegram_project(row) for row in await cursor.fetchall()]

    async def update_telegram_project(
        self,
        user_id: int,
        project_id: str,
        *,
        name: str | None = None,
        emoji: str | None = None,
        instructions: str | None = None,
    ) -> TelegramProject | None:
        current = await self.telegram_project(user_id, project_id)
        if current is None:
            return None
        await self._write(
            """UPDATE telegram_projects
               SET name = ?, emoji = ?, instructions = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND user_id = ?""",
            (
                current.name if name is None else name,
                current.emoji if emoji is None else emoji,
                current.instructions if instructions is None else instructions,
                project_id,
                user_id,
            ),
        )
        return await self.telegram_project(user_id, project_id)

    async def set_telegram_conversation(
        self, user_id: int, project_id: str, conversation_id: str | None
    ) -> None:
        await self._write(
            """UPDATE telegram_projects
               SET openai_conversation_id = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND user_id = ?""",
            (conversation_id, project_id, user_id),
        )

    async def touch_telegram_project(self, user_id: int, project_id: str) -> None:
        await self._write(
            """UPDATE telegram_projects
               SET updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND user_id = ?""",
            (project_id, user_id),
        )

    async def delete_telegram_project(
        self, user_id: int, project_id: str
    ) -> TelegramProject | None:
        current = await self.telegram_project(user_id, project_id)
        if current is None:
            return None
        if current.kind == "skye":
            raise PermissionError("The Skye project cannot be deleted.")
        await self._write(
            "DELETE FROM telegram_projects WHERE id = ? AND user_id = ? AND kind = 'custom'",
            (project_id, user_id),
        )
        return current

    @staticmethod
    def _telegram_project(row: aiosqlite.Row) -> TelegramProject:
        return TelegramProject(
            id=cast(str, row["id"]),
            user_id=int(row["user_id"]),
            kind=cast(ProjectKind, row["kind"]),
            name=cast(str, row["name"]),
            emoji=cast(str, row["emoji"]),
            instructions=cast(str, row["instructions"]),
            openai_conversation_id=cast(str | None, row["openai_conversation_id"]),
            created_at=cast(str, row["created_at"]),
            updated_at=cast(str, row["updated_at"]),
        )

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

    async def save_media_group_item(self, item: MediaGroupItem) -> None:
        await self._write(
            """INSERT INTO media_group_items (
                   chat_id, media_group_id, message_id, thread_id, media_kind,
                   file_id, file_unique_id, file_name, mime_type, file_size,
                   width, height, caption, sent_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, message_id) DO UPDATE SET
                   media_group_id = excluded.media_group_id,
                   thread_id = excluded.thread_id,
                   media_kind = excluded.media_kind,
                   file_id = excluded.file_id,
                   file_unique_id = excluded.file_unique_id,
                   file_name = excluded.file_name,
                   mime_type = excluded.mime_type,
                   file_size = excluded.file_size,
                   width = excluded.width,
                   height = excluded.height,
                   caption = excluded.caption,
                   sent_at = excluded.sent_at""",
            (
                item.chat_id,
                item.media_group_id,
                item.message_id,
                item.thread_id,
                item.media_kind,
                item.file_id,
                item.file_unique_id,
                item.file_name,
                item.mime_type,
                item.file_size,
                item.width,
                item.height,
                item.caption,
                item.sent_at,
            ),
        )

    async def media_group_id_for_message(self, chat_id: int, message_id: int) -> str | None:
        cursor = await self.conn.execute(
            """SELECT media_group_id FROM media_group_items
               WHERE chat_id = ? AND message_id = ?""",
            (chat_id, message_id),
        )
        row = await cursor.fetchone()
        return cast(str | None, row["media_group_id"]) if row else None

    async def media_group_items(
        self, chat_id: int, media_group_id: str
    ) -> list[MediaGroupItem]:
        cursor = await self.conn.execute(
            """SELECT * FROM media_group_items
               WHERE chat_id = ? AND media_group_id = ?
               ORDER BY message_id""",
            (chat_id, media_group_id),
        )
        return [self._media_group_item(row) for row in await cursor.fetchall()]

    async def claim_media_group(self, chat_id: int, media_group_id: str, message_id: int) -> bool:
        cursor = await self._write(
            """INSERT INTO media_group_claims (chat_id, media_group_id, claimed_message_id)
               VALUES (?, ?, ?)
               ON CONFLICT(chat_id, media_group_id) DO NOTHING""",
            (chat_id, media_group_id, message_id),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _media_group_item(row: aiosqlite.Row) -> MediaGroupItem:
        return MediaGroupItem(
            chat_id=int(row["chat_id"]),
            media_group_id=str(row["media_group_id"]),
            message_id=int(row["message_id"]),
            thread_id=int(row["thread_id"]),
            media_kind=str(row["media_kind"]),
            file_id=str(row["file_id"]),
            file_unique_id=str(row["file_unique_id"]),
            file_name=cast(str | None, row["file_name"]),
            mime_type=cast(str | None, row["mime_type"]),
            file_size=cast(int | None, row["file_size"]),
            width=cast(int | None, row["width"]),
            height=cast(int | None, row["height"]),
            caption=cast(str | None, row["caption"]),
            sent_at=int(row["sent_at"]),
        )

    async def group_messages(
        self,
        chat_id: int,
        thread_id: int,
        *,
        before: int,
        limit: int,
        after: int = 0,
    ) -> list[GroupMessage]:
        cursor = await self.conn.execute(
            """SELECT * FROM group_messages
               WHERE chat_id = ? AND thread_id = ? AND message_id > ? AND message_id < ?
               ORDER BY message_id DESC LIMIT ?""",
            (chat_id, thread_id, after, before, limit),
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

    async def list_custom_connectors(self, user_id: int) -> list[CustomConnector]:
        cursor = await self.conn.execute(
            """SELECT * FROM custom_connectors WHERE user_id = ?
               ORDER BY lower(name), updated_at DESC""",
            (user_id,),
        )
        return [self._custom_connector(row) for row in await cursor.fetchall()]

    async def get_custom_connector(self, user_id: int, connector_id: str) -> CustomConnector | None:
        cursor = await self.conn.execute(
            "SELECT * FROM custom_connectors WHERE id = ? AND user_id = ?",
            (connector_id, user_id),
        )
        row = await cursor.fetchone()
        return self._custom_connector(row) if row else None

    async def save_custom_connector(self, connector: CustomConnector) -> CustomConnector:
        await self._write(
            """INSERT INTO custom_connectors (
                   id, user_id, name, url, headers, enabled, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                   name = excluded.name,
                   url = excluded.url,
                   headers = excluded.headers,
                   enabled = excluded.enabled,
                   updated_at = CURRENT_TIMESTAMP
               WHERE custom_connectors.user_id = excluded.user_id""",
            (
                connector.id,
                connector.user_id,
                connector.name,
                connector.url,
                json.dumps(connector.headers, separators=(",", ":")),
                int(connector.enabled),
            ),
        )
        saved = await self.get_custom_connector(connector.user_id, connector.id)
        if saved is None:
            raise LookupError("Connector not found.")
        return saved

    async def delete_custom_connector(self, user_id: int, connector_id: str) -> bool:
        cursor = await self._write(
            "DELETE FROM custom_connectors WHERE id = ? AND user_id = ?",
            (connector_id, user_id),
        )
        return cursor.rowcount > 0

    async def list_skills(self, scope: Scope) -> list[Skill]:
        cursor = await self.conn.execute(
            """SELECT id, scope_kind, scope_id, openai_skill_id, name, description, filename,
                      file_count, created_by, created_at
               FROM skills WHERE scope_kind = ? AND scope_id = ?
               ORDER BY lower(name), created_at""",
            (scope.kind, scope.id),
        )
        return [self._skill(row) for row in await cursor.fetchall()]

    async def get_skill(self, scope: Scope, skill_id: str) -> Skill | None:
        cursor = await self.conn.execute(
            "SELECT * FROM skills WHERE id = ? AND scope_kind = ? AND scope_id = ?",
            (skill_id, scope.kind, scope.id),
        )
        row = await cursor.fetchone()
        return self._skill(row) if row else None

    async def save_skill(self, skill: Skill) -> Skill:
        await self._write(
            """INSERT INTO skills (
                   id, scope_kind, scope_id, openai_skill_id, name, description,
                   filename, archive, file_count, created_by
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                skill.id,
                skill.scope.kind,
                skill.scope.id,
                skill.openai_skill_id,
                skill.name,
                skill.description,
                skill.filename,
                skill.archive,
                skill.file_count,
                skill.created_by,
            ),
        )
        saved = await self.get_skill(skill.scope, skill.id)
        if saved is None:
            raise LookupError("Skill not found.")
        return saved

    async def delete_skill(self, scope: Scope, skill_id: str) -> Skill | None:
        current = await self.get_skill(scope, skill_id)
        if current is None:
            return None
        await self._write(
            "DELETE FROM skills WHERE id = ? AND scope_kind = ? AND scope_id = ?",
            (skill_id, scope.kind, scope.id),
        )
        return current

    @staticmethod
    def _skill(row: aiosqlite.Row) -> Skill:
        try:
            raw_archive = row["archive"]
        except IndexError:
            raw_archive = None
        archive = bytes(raw_archive) if raw_archive is not None else b""
        return Skill(
            id=cast(str, row["id"]),
            scope=Scope(cast(ScopeKind, row["scope_kind"]), int(row["scope_id"])),
            openai_skill_id=cast(str, row["openai_skill_id"]),
            name=cast(str, row["name"]),
            description=cast(str, row["description"]),
            filename=cast(str, row["filename"]),
            file_count=int(row["file_count"]),
            created_by=int(row["created_by"]),
            created_at=cast(str, row["created_at"]),
            archive=archive,
        )

    async def list_user_toolkits(self, user_id: int) -> list[str]:
        cursor = await self.conn.execute(
            "SELECT slug FROM user_toolkits WHERE user_id = ? ORDER BY slug",
            (user_id,),
        )
        return [cast(str, row["slug"]) for row in await cursor.fetchall()]

    async def add_user_toolkit(self, user_id: int, slug: str) -> None:
        await self._write(
            "INSERT OR IGNORE INTO user_toolkits (user_id, slug) VALUES (?, ?)",
            (user_id, slug),
        )

    async def remove_user_toolkit(self, user_id: int, slug: str) -> bool:
        cursor = await self._write(
            "DELETE FROM user_toolkits WHERE user_id = ? AND slug = ?",
            (user_id, slug),
        )
        return cursor.rowcount > 0

    async def composio_session(
        self, user_id: int, toolkit_key: str
    ) -> tuple[str, str, dict[str, str]] | None:
        cursor = await self.conn.execute(
            """SELECT session_id, mcp_url, mcp_headers FROM composio_session_cache
               WHERE user_id = ? AND toolkit_key = ?""",
            (user_id, toolkit_key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        raw = json.loads(cast(str, row["mcp_headers"] or "{}"))
        headers = {
            str(key): str(value)
            for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        return str(row["session_id"]), str(row["mcp_url"]), headers

    async def save_composio_session(
        self,
        user_id: int,
        session_id: str,
        mcp_url: str,
        toolkit_key: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        await self._write(
            """INSERT INTO composio_session_cache
               (user_id, toolkit_key, session_id, mcp_url, mcp_headers)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, toolkit_key) DO UPDATE SET
                   session_id = excluded.session_id,
                   mcp_url = excluded.mcp_url,
                   mcp_headers = excluded.mcp_headers,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                user_id,
                toolkit_key,
                session_id,
                mcp_url,
                json.dumps(dict(headers or {}), separators=(",", ":")),
            ),
        )

    async def delete_composio_sessions(self, user_id: int) -> None:
        await self._write("DELETE FROM composio_session_cache WHERE user_id = ?", (user_id,))

    async def chat_title(self, chat_id: int) -> str:
        cursor = await self.conn.execute(
            "SELECT title FROM known_chats WHERE chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        return str(row["title"]) if row else "this group"

    async def remember_chat(self, chat_id: int, title: str) -> None:
        label = " ".join(title.split())[:128] or "Group"
        await self._write(
            """INSERT INTO known_chats (chat_id, title)
               VALUES (?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   title = excluded.title,
                   updated_at = CURRENT_TIMESTAMP""",
            (chat_id, label),
        )

    async def shareable_groups(self, user_id: int) -> list[KnownGroup]:
        cursor = await self.conn.execute(
            """SELECT m.chat_id, COALESCE(k.title, 'Group') AS title
               FROM group_messages AS m
               JOIN access_entries AS a
                 ON a.kind = 'chat' AND a.telegram_id = m.chat_id AND a.effect = 'allow'
               LEFT JOIN known_chats AS k ON k.chat_id = m.chat_id
               WHERE m.sender_id = ?
               GROUP BY m.chat_id
               ORDER BY lower(title), m.chat_id""",
            (user_id,),
        )
        return [
            KnownGroup(int(row["chat_id"]), str(row["title"])) for row in await cursor.fetchall()
        ]

    async def save_connector_share(self, share: ConnectorShare) -> ConnectorShare:
        await self._write(
            """INSERT INTO connector_shares (
                   id, chat_id, owner_id, owner_name, kind, ref
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, owner_id, kind, ref) DO UPDATE SET
                   owner_name = excluded.owner_name""",
            (share.id, share.chat_id, share.owner_id, share.owner_name, share.kind, share.ref),
        )
        saved = await self.connector_share_by_target(
            share.chat_id, share.owner_id, share.kind, share.ref
        )
        if saved is None:
            raise RuntimeError("Share was not saved")
        return saved

    async def connector_share(self, share_id: str) -> ConnectorShare | None:
        cursor = await self.conn.execute(
            """SELECT s.id, s.chat_id, COALESCE(k.title, 'Group') AS chat_title,
                      s.owner_id, s.owner_name, s.kind, s.ref, s.created_at
               FROM connector_shares AS s
               LEFT JOIN known_chats AS k ON k.chat_id = s.chat_id
               WHERE s.id = ?""",
            (share_id,),
        )
        row = await cursor.fetchone()
        return self._connector_share(row) if row else None

    async def connector_share_by_target(
        self, chat_id: int, owner_id: int, kind: ConnectorKind, ref: str
    ) -> ConnectorShare | None:
        cursor = await self.conn.execute(
            """SELECT s.id, s.chat_id, COALESCE(k.title, 'Group') AS chat_title,
                      s.owner_id, s.owner_name, s.kind, s.ref, s.created_at
               FROM connector_shares AS s
               LEFT JOIN known_chats AS k ON k.chat_id = s.chat_id
               WHERE s.chat_id = ? AND s.owner_id = ? AND s.kind = ? AND s.ref = ?""",
            (chat_id, owner_id, kind, ref),
        )
        row = await cursor.fetchone()
        return self._connector_share(row) if row else None

    async def list_connector_shares(
        self,
        *,
        chat_id: int | None = None,
        owner_id: int | None = None,
        kind: ConnectorKind | None = None,
        ref: str | None = None,
    ) -> list[ConnectorShare]:
        clauses = ["1 = 1"]
        params: list[object] = []
        if chat_id is not None:
            clauses.append("s.chat_id = ?")
            params.append(chat_id)
        if owner_id is not None:
            clauses.append("s.owner_id = ?")
            params.append(owner_id)
        if kind is not None:
            clauses.append("s.kind = ?")
            params.append(kind)
        if ref is not None:
            clauses.append("s.ref = ?")
            params.append(ref)
        cursor = await self.conn.execute(
            """SELECT s.id, s.chat_id, COALESCE(k.title, 'Group') AS chat_title,
                      s.owner_id, s.owner_name, s.kind, s.ref, s.created_at
               FROM connector_shares AS s
               LEFT JOIN known_chats AS k ON k.chat_id = s.chat_id
               WHERE """
            + " AND ".join(clauses)
            + " ORDER BY lower(s.owner_name), s.created_at",
            params,
        )
        return [self._connector_share(row) for row in await cursor.fetchall()]

    async def count_connector_shares(
        self, *, chat_id: int | None = None, owner_id: int | None = None
    ) -> int:
        clauses = ["1 = 1"]
        params: list[object] = []
        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM connector_shares WHERE " + " AND ".join(clauses),
            params,
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def delete_connector_share(self, share_id: str) -> bool:
        cursor = await self._write("DELETE FROM connector_shares WHERE id = ?", (share_id,))
        return cursor.rowcount > 0

    async def delete_connector_shares(
        self, *, owner_id: int, kind: ConnectorKind | None = None, ref: str | None = None
    ) -> None:
        if kind is None:
            await self._write("DELETE FROM connector_shares WHERE owner_id = ?", (owner_id,))
            return
        if ref is None:
            await self._write(
                "DELETE FROM connector_shares WHERE owner_id = ? AND kind = ?",
                (owner_id, kind),
            )
            return
        await self._write(
            "DELETE FROM connector_shares WHERE owner_id = ? AND kind = ? AND ref = ?",
            (owner_id, kind, ref),
        )

    @staticmethod
    def _connector_share(row: aiosqlite.Row) -> ConnectorShare:
        return ConnectorShare(
            id=cast(str, row["id"]),
            chat_id=cast(int, row["chat_id"]),
            chat_title=cast(str, row["chat_title"]),
            owner_id=cast(int, row["owner_id"]),
            owner_name=cast(str, row["owner_name"]),
            kind=cast(ConnectorKind, row["kind"]),
            ref=cast(str, row["ref"]),
            name=cast(str, row["ref"]),
            available=True,
            created_at=cast(str, row["created_at"]),
        )

    @staticmethod
    def _custom_connector(row: aiosqlite.Row) -> CustomConnector:
        raw = json.loads(cast(str, row["headers"]) or "{}")
        headers = {
            str(key): str(value)
            for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        return CustomConnector(
            id=cast(str, row["id"]),
            user_id=cast(int, row["user_id"]),
            name=cast(str, row["name"]),
            url=cast(str, row["url"]),
            headers=headers,
            enabled=bool(row["enabled"]),
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

    async def drop_pending_updates(self) -> int:
        cursor = await self._write(
            """UPDATE updates
               SET state = 'done', last_error = NULL, updated_at = CURRENT_TIMESTAMP
               WHERE state IN ('pending', 'processing')"""
        )
        return max(cursor.rowcount, 0)

    @staticmethod
    def encode_payload(payload: dict[str, Any]) -> str:
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    async def create_web_session(self, session: WebSession) -> WebSession:
        await self._write(
            """INSERT INTO web_sessions (id, user_id, display_name, username, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                session.id,
                session.user_id,
                session.display_name,
                session.username,
                session.expires_at,
            ),
        )
        return session

    async def web_session(self, session_id: str) -> WebSession | None:
        cursor = await self.conn.execute(
            """SELECT * FROM web_sessions
               WHERE id = ? AND expires_at > CURRENT_TIMESTAMP""",
            (session_id,),
        )
        row = await cursor.fetchone()
        return self._web_session(row) if row else None

    async def delete_web_session(self, session_id: str) -> None:
        await self._write("DELETE FROM web_sessions WHERE id = ?", (session_id,))

    async def purge_web_sessions(self) -> None:
        await self._write("DELETE FROM web_sessions WHERE expires_at <= CURRENT_TIMESTAMP")

    @staticmethod
    def _web_session(row: aiosqlite.Row) -> WebSession:
        return WebSession(
            id=cast(str, row["id"]),
            user_id=int(row["user_id"]),
            display_name=cast(str, row["display_name"]),
            username=cast(str | None, row["username"]),
            created_at=cast(str, row["created_at"]),
            expires_at=cast(str, row["expires_at"]),
        )

    async def create_web_project(self, project: WebProject) -> WebProject:
        await self._write(
            """INSERT INTO web_projects (
                   id, user_id, kind, name, instructions, icon, color, pinned,
                   openai_conversation_id, last_message_preview, last_message_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project.id,
                project.user_id,
                project.kind,
                project.name,
                project.instructions,
                project.icon,
                project.color,
                int(project.pinned),
                project.openai_conversation_id,
                project.last_message_preview,
                project.last_message_at,
            ),
        )
        saved = await self.web_project(project.user_id, project.id)
        if saved is None:
            raise RuntimeError("Project was not created")
        return saved

    async def web_project(self, user_id: int, project_id: str) -> WebProject | None:
        cursor = await self.conn.execute(
            "SELECT * FROM web_projects WHERE id = ? AND user_id = ?",
            (project_id, user_id),
        )
        row = await cursor.fetchone()
        return self._web_project(row) if row else None

    async def skye_web_project(self, user_id: int) -> WebProject | None:
        cursor = await self.conn.execute(
            "SELECT * FROM web_projects WHERE user_id = ? AND kind = 'skye'",
            (user_id,),
        )
        row = await cursor.fetchone()
        return self._web_project(row) if row else None

    async def list_web_projects(self, user_id: int) -> list[WebProject]:
        cursor = await self.conn.execute(
            """SELECT * FROM web_projects WHERE user_id = ?
               ORDER BY pinned DESC,
                        COALESCE(last_message_at, created_at) DESC,
                        created_at DESC""",
            (user_id,),
        )
        return [self._web_project(row) for row in await cursor.fetchall()]

    async def update_web_project(
        self,
        user_id: int,
        project_id: str,
        *,
        name: str | None = None,
        instructions: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        pinned: bool | None = None,
    ) -> WebProject | None:
        current = await self.web_project(user_id, project_id)
        if current is None:
            return None
        updated = WebProject(
            id=current.id,
            user_id=current.user_id,
            kind=current.kind,
            name=current.name if name is None else name,
            instructions=current.instructions if instructions is None else instructions,
            icon=current.icon if icon is None else icon,
            color=current.color if color is None else color,
            pinned=current.pinned if pinned is None else pinned,
            openai_conversation_id=current.openai_conversation_id,
            last_message_preview=current.last_message_preview,
            last_message_at=current.last_message_at,
            created_at=current.created_at,
            updated_at=current.updated_at,
        )
        await self._write(
            """UPDATE web_projects
               SET name = ?, instructions = ?, icon = ?, color = ?, pinned = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND user_id = ?""",
            (
                updated.name,
                updated.instructions,
                updated.icon,
                updated.color,
                int(updated.pinned),
                project_id,
                user_id,
            ),
        )
        return await self.web_project(user_id, project_id)

    async def set_web_conversation(
        self, user_id: int, project_id: str, conversation_id: str | None
    ) -> None:
        await self._write(
            """UPDATE web_projects
               SET openai_conversation_id = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND user_id = ?""",
            (conversation_id, project_id, user_id),
        )

    async def touch_web_project(self, user_id: int, project_id: str, preview: str) -> None:
        await self._write(
            """UPDATE web_projects
               SET last_message_preview = ?, last_message_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND user_id = ?""",
            (preview[:240], project_id, user_id),
        )

    async def delete_web_project(self, user_id: int, project_id: str) -> WebProject | None:
        current = await self.web_project(user_id, project_id)
        if current is None:
            return None
        if current.kind == "skye":
            raise PermissionError("The Skye project cannot be deleted.")
        await self._write(
            "DELETE FROM web_projects WHERE id = ? AND user_id = ? AND kind = 'custom'",
            (project_id, user_id),
        )
        return current

    @staticmethod
    def _web_project(row: aiosqlite.Row) -> WebProject:
        return WebProject(
            id=cast(str, row["id"]),
            user_id=int(row["user_id"]),
            kind=cast(ProjectKind, row["kind"]),
            name=cast(str, row["name"]),
            instructions=cast(str, row["instructions"]),
            icon=cast(str, row["icon"]),
            color=cast(str, row["color"]),
            pinned=bool(row["pinned"]),
            openai_conversation_id=cast(str | None, row["openai_conversation_id"]),
            last_message_preview=cast(str, row["last_message_preview"]),
            last_message_at=cast(str | None, row["last_message_at"]),
            created_at=cast(str, row["created_at"]),
            updated_at=cast(str, row["updated_at"]),
        )

    async def add_web_message(self, message: WebMessage) -> WebMessage:
        await self._write(
            """INSERT INTO web_messages (
                   id, project_id, user_id, role, text, tool_name, tool_status, file_ids
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message.id,
                message.project_id,
                message.user_id,
                message.role,
                message.text,
                message.tool_name,
                message.tool_status,
                json.dumps(list(message.file_ids), separators=(",", ":")),
            ),
        )
        saved = await self.web_message(message.user_id, message.id)
        if saved is None:
            raise RuntimeError("Message was not saved")
        return saved

    async def web_message(self, user_id: int, message_id: str) -> WebMessage | None:
        cursor = await self.conn.execute(
            "SELECT * FROM web_messages WHERE id = ? AND user_id = ?",
            (message_id, user_id),
        )
        row = await cursor.fetchone()
        return self._web_message(row) if row else None

    async def list_web_messages(
        self, user_id: int, project_id: str, *, after_id: str | None = None, limit: int = 200
    ) -> list[WebMessage]:
        if after_id:
            cursor = await self.conn.execute(
                """SELECT * FROM web_messages
                   WHERE project_id = ? AND user_id = ? AND created_at >= (
                       SELECT created_at FROM web_messages WHERE id = ?
                   ) AND id != ?
                   ORDER BY created_at, id LIMIT ?""",
                (project_id, user_id, after_id, after_id, limit),
            )
        else:
            cursor = await self.conn.execute(
                """SELECT * FROM web_messages
                   WHERE project_id = ? AND user_id = ?
                   ORDER BY created_at, id LIMIT ?""",
                (project_id, user_id, limit),
            )
        return [self._web_message(row) for row in await cursor.fetchall()]

    async def clear_web_messages(self, user_id: int, project_id: str) -> None:
        await self._write(
            "DELETE FROM web_messages WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        )

    async def search_web(
        self, user_id: int, query: str, limit: int = 20
    ) -> tuple[list[WebProject], list[tuple[WebProject, WebMessage]]]:
        terms = re.findall(r"\w+", query.casefold(), flags=re.UNICODE)[:12]
        if not terms:
            return [], []
        like = "%" + "%".join(terms[:4]) + "%"
        cursor = await self.conn.execute(
            """SELECT * FROM web_projects
               WHERE user_id = ? AND name LIKE ? COLLATE NOCASE
               ORDER BY pinned DESC, COALESCE(last_message_at, created_at) DESC LIMIT ?""",
            (user_id, like, limit),
        )
        projects = [self._web_project(row) for row in await cursor.fetchall()]
        match = " OR ".join(f'"{term}"' for term in terms)
        cursor = await self.conn.execute(
            """SELECT m.*, p.id AS p_id, p.user_id AS p_user_id, p.kind, p.name AS p_name,
                      p.instructions, p.icon, p.color, p.pinned, p.openai_conversation_id,
                      p.last_message_preview, p.last_message_at, p.created_at AS p_created_at,
                      p.updated_at AS p_updated_at
               FROM web_messages_fts
               JOIN web_messages AS m ON m.rowid = web_messages_fts.rowid
               JOIN web_projects AS p ON p.id = m.project_id
               WHERE web_messages_fts MATCH ? AND m.user_id = ? AND p.user_id = ?
               ORDER BY bm25(web_messages_fts), m.created_at DESC LIMIT ?""",
            (match, user_id, user_id, limit),
        )
        messages: list[tuple[WebProject, WebMessage]] = []
        for row in await cursor.fetchall():
            project = WebProject(
                id=cast(str, row["p_id"]),
                user_id=int(row["p_user_id"]),
                kind=cast(ProjectKind, row["kind"]),
                name=cast(str, row["p_name"]),
                instructions=cast(str, row["instructions"]),
                icon=cast(str, row["icon"]),
                color=cast(str, row["color"]),
                pinned=bool(row["pinned"]),
                openai_conversation_id=cast(str | None, row["openai_conversation_id"]),
                last_message_preview=cast(str, row["last_message_preview"]),
                last_message_at=cast(str | None, row["last_message_at"]),
                created_at=cast(str, row["p_created_at"]),
                updated_at=cast(str, row["p_updated_at"]),
            )
            messages.append((project, self._web_message(row)))
        return projects, messages

    @staticmethod
    def _web_message(row: aiosqlite.Row) -> WebMessage:
        raw_ids = json.loads(cast(str, row["file_ids"] or "[]"))
        file_ids = tuple(str(item) for item in raw_ids if isinstance(item, str))
        status = cast(str | None, row["tool_status"])
        return WebMessage(
            id=cast(str, row["id"]),
            project_id=cast(str, row["project_id"]),
            user_id=int(row["user_id"]),
            role=cast(WebMessageRole, row["role"]),
            text=cast(str, row["text"]),
            tool_name=cast(str | None, row["tool_name"]),
            tool_status=cast(ToolStatus | None, status if status in {"running", "done"} else None),
            file_ids=file_ids,
            created_at=cast(str, row["created_at"]),
        )

    async def add_web_file(self, file: WebFile) -> WebFile:
        await self._write(
            """INSERT INTO web_files (id, user_id, project_id, filename, mime, size, kind)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                file.id,
                file.user_id,
                file.project_id,
                file.filename,
                file.mime,
                file.size,
                file.kind,
            ),
        )
        saved = await self.web_file(file.user_id, file.id)
        if saved is None:
            raise RuntimeError("File was not saved")
        return saved

    async def web_file(self, user_id: int, file_id: str) -> WebFile | None:
        cursor = await self.conn.execute(
            "SELECT * FROM web_files WHERE id = ? AND user_id = ?",
            (file_id, user_id),
        )
        row = await cursor.fetchone()
        return self._web_file(row) if row else None

    async def list_web_files(self, user_id: int, project_id: str) -> list[WebFile]:
        cursor = await self.conn.execute(
            "SELECT * FROM web_files WHERE user_id = ? AND project_id = ? ORDER BY created_at",
            (user_id, project_id),
        )
        return [self._web_file(row) for row in await cursor.fetchall()]

    @staticmethod
    def _web_file(row: aiosqlite.Row) -> WebFile:
        return WebFile(
            id=cast(str, row["id"]),
            user_id=int(row["user_id"]),
            project_id=cast(str, row["project_id"]),
            filename=cast(str, row["filename"]),
            mime=cast(str, row["mime"]),
            size=int(row["size"]),
            kind=cast(WebFileKind, row["kind"]),
            created_at=cast(str, row["created_at"]),
        )
