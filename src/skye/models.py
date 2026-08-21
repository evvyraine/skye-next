from dataclasses import dataclass
from typing import Literal

from .config import ModelId, Reasoning

ScopeKind = Literal["user", "chat"]
ChatType = Literal["private", "group", "supergroup", "channel"]
AccessEffect = Literal["allow", "ban"]
MemoryCategory = Literal["preference", "personal", "project", "instruction", "other"]
AgentVisibility = Literal["private", "unlisted", "public"]
AgentCapability = Literal["web", "image", "shell"]
ConnectorKind = Literal["app", "custom"]
ProjectKind = Literal["skye", "custom"]
WebMessageRole = Literal["user", "assistant", "tool", "system"]
WebFileKind = Literal["upload", "image", "document"]
ToolStatus = Literal["running", "done"]


@dataclass(frozen=True, slots=True)
class Scope:
    kind: ScopeKind
    id: int


@dataclass(frozen=True, slots=True)
class AccessEntry:
    scope: Scope
    effect: AccessEffect
    created_by: int
    created_at: str


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
    active_agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentProfile:
    id: str
    owner_id: int
    visibility: AgentVisibility
    current_version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class AgentVersion:
    agent_id: str
    version: int
    name: str
    description: str
    instructions: str
    model: ModelId | None
    capabilities: tuple[AgentCapability, ...]
    checksum: str
    share_token: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class InstalledAgent:
    scope: Scope
    profile: AgentProfile
    version: AgentVersion
    enabled: bool
    installed_by: int
    installed_at: str


@dataclass(frozen=True, slots=True)
class Skill:
    id: str
    scope: Scope
    openai_skill_id: str
    name: str
    description: str
    filename: str
    file_count: int
    created_by: int
    created_at: str
    archive: bytes = b""


@dataclass(frozen=True, slots=True)
class CustomConnector:
    id: str
    user_id: int
    name: str
    url: str
    headers: dict[str, str]
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class AppConnector:
    slug: str
    name: str
    status: Literal["connected", "pending", "available"]
    account_id: str | None = None
    no_auth: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class ConnectorSnapshot:
    apps: tuple[AppConnector, ...]
    custom: tuple[CustomConnector, ...]

    @property
    def connected_count(self) -> int:
        return sum(1 for item in self.apps if item.status == "connected") + sum(
            1 for item in self.custom if item.enabled
        )

    @property
    def labels(self) -> tuple[str, ...]:
        names = [item.name for item in self.apps if item.status == "connected"]
        names.extend(item.name for item in self.custom if item.enabled)
        return tuple(names)


@dataclass(frozen=True, slots=True)
class KnownGroup:
    chat_id: int
    title: str


@dataclass(frozen=True, slots=True)
class ConnectorShare:
    id: str
    chat_id: int
    chat_title: str
    owner_id: int
    owner_name: str
    kind: ConnectorKind
    ref: str
    name: str
    available: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class Memory:
    id: int
    scope: Scope
    category: MemoryCategory
    content: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class WebSession:
    id: str
    user_id: int
    display_name: str
    username: str | None
    created_at: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class TelegramProject:
    id: str
    user_id: int
    kind: ProjectKind
    name: str
    emoji: str
    instructions: str
    openai_conversation_id: str | None
    created_at: str
    updated_at: str

    @property
    def label(self) -> str:
        text = f"{self.emoji} {self.name}".strip()
        return text[:64] if len(text) <= 64 else text[:63].rstrip() + "…"

    @property
    def deletable(self) -> bool:
        return self.kind != "skye"


@dataclass(frozen=True, slots=True)
class WebProject:
    id: str
    user_id: int
    kind: ProjectKind
    name: str
    instructions: str
    icon: str
    color: str
    pinned: bool
    openai_conversation_id: str | None
    last_message_preview: str
    last_message_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class WebMessage:
    id: str
    project_id: str
    user_id: int
    role: WebMessageRole
    text: str
    tool_name: str | None
    tool_status: ToolStatus | None
    file_ids: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class WebFile:
    id: str
    user_id: int
    project_id: str
    filename: str
    mime: str
    size: int
    kind: WebFileKind
    created_at: str


@dataclass(frozen=True, slots=True)
class WebSearchHit:
    project: WebProject
    message: WebMessage | None = None


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
