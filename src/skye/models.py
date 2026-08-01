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
