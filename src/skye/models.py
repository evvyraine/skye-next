from dataclasses import dataclass
from typing import Literal

from .config import ModelId, Reasoning

ScopeKind = Literal["user", "chat"]
ChatType = Literal["private", "group", "supergroup", "channel"]
MemoryCategory = Literal["preference", "personal", "project", "instruction", "other"]


@dataclass(frozen=True, slots=True)
class Scope:
    kind: ScopeKind
    id: int


@dataclass(frozen=True, slots=True)
class RequestContext:
    chat_id: int
    chat_type: ChatType
    user_id: int
    thread_id: int = 0
    username: str | None = None
    display_name: str = "User"

    @property
    def scope(self) -> Scope:
        if self.chat_type == "private":
            return Scope("user", self.user_id)
        return Scope("chat", self.chat_id)


@dataclass(frozen=True, slots=True)
class ChatSettings:
    model: ModelId
    reasoning: Reasoning
    memory_enabled: bool = True


@dataclass(frozen=True, slots=True)
class Memory:
    id: int
    scope: Scope
    category: MemoryCategory
    content: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class GroupMessage:
    chat_id: int
    thread_id: int
    message_id: int
    sender_id: int | None
    sender_name: str
    sender_username: str | None
    text: str
    media_kind: str | None
    media_file_id: str | None
    reply_to_message_id: int | None
    reply_sender_name: str | None
    reply_sender_username: str | None
    reply_excerpt: str | None
    sent_at: int
