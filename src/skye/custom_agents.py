from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass

from .config import ModelId
from .db import Database
from .models import AgentCapability, AgentVersion, InstalledAgent, Scope

AGENT_CAPABILITIES: tuple[AgentCapability, ...] = ("web", "image", "shell")
SHARE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@dataclass(frozen=True, slots=True)
class AgentComposition:
    active: InstalledAgent | None
    specialists: tuple[InstalledAgent, ...]


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    name: str
    description: str
    instructions: str
    model: ModelId | None
    capabilities: tuple[AgentCapability, ...]
    checksum: str


class CustomAgentService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create(
        self,
        *,
        owner_id: int,
        scope: Scope,
        name: str,
        description: str,
        instructions: str,
        model: ModelId | None = None,
        capabilities: tuple[AgentCapability, ...] = AGENT_CAPABILITIES,
    ) -> InstalledAgent:
        definition = self._definition(name, description, instructions, model, capabilities)
        return await self.database.create_agent(
            agent_id=uuid.uuid4().hex[:16],
            owner_id=owner_id,
            scope=scope,
            name=definition.name,
            description=definition.description,
            instructions=definition.instructions,
            model=definition.model,
            capabilities=definition.capabilities,
            checksum=definition.checksum,
        )

    async def edit(
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
    ) -> InstalledAgent:
        definition = self._definition(name, description, instructions, model, capabilities)
        return await self.database.create_agent_version(
            agent_id=agent_id,
            owner_id=owner_id,
            scope=scope,
            name=definition.name,
            description=definition.description,
            instructions=definition.instructions,
            model=definition.model,
            capabilities=definition.capabilities,
            checksum=definition.checksum,
        )

    async def reconfigure(
        self,
        *,
        agent_id: str,
        owner_id: int,
        scope: Scope,
        model: ModelId | None = None,
        capabilities: tuple[AgentCapability, ...] | None = None,
        keep_model: bool = False,
    ) -> InstalledAgent:
        installed = await self.require_installed(scope, agent_id)
        version = installed.version
        return await self.edit(
            agent_id=agent_id,
            owner_id=owner_id,
            scope=scope,
            name=version.name,
            description=version.description,
            instructions=version.instructions,
            model=version.model if keep_model else model,
            capabilities=capabilities if capabilities is not None else version.capabilities,
        )

    async def list(self, scope: Scope) -> list[InstalledAgent]:
        return await self.database.installed_agents(scope)

    async def require_installed(self, scope: Scope, agent_id: str) -> InstalledAgent:
        installed = await self.database.installed_agent(scope, agent_id)
        if installed is None:
            raise LookupError("Agent is not installed in this chat.")
        return installed

    async def select(self, scope: Scope, agent_id: str | None) -> None:
        if agent_id is not None:
            installed = await self.require_installed(scope, agent_id)
            if not installed.enabled:
                raise ValueError("Agent is disabled.")
        await self.database.set_active_agent(scope, agent_id)

    async def remove(self, scope: Scope, agent_id: str) -> bool:
        return await self.database.remove_agent_install(scope, agent_id)

    async def share(self, scope: Scope, agent_id: str, owner_id: int) -> str:
        installed = await self.require_installed(scope, agent_id)
        token = installed.version.share_token or secrets.token_urlsafe(18)
        return await self.database.share_agent_version(
            agent_id, owner_id, installed.version.version, token
        )

    async def import_shared(
        self, scope: Scope, token_or_link: str, installed_by: int
    ) -> InstalledAgent:
        token = self.share_token(token_or_link)
        shared = await self.database.shared_agent(token)
        if shared is None:
            raise LookupError("Shared agent not found.")
        profile, version = shared
        return await self.database.install_agent(
            scope, profile.id, version.version, installed_by
        )

    async def composition(self, scope: Scope, active_agent_id: str | None) -> AgentComposition:
        installed = await self.database.installed_agents(scope, enabled_only=True)
        active = next(
            (item for item in installed if item.profile.id == active_agent_id), None
        )
        specialists = tuple(
            item for item in installed if active is None or item.profile.id != active.profile.id
        )
        return AgentComposition(active, specialists)

    async def active_name(self, scope: Scope, active_agent_id: str | None) -> str:
        if active_agent_id is None:
            return "Skye"
        installed = await self.database.installed_agent(scope, active_agent_id)
        return installed.version.name if installed and installed.enabled else "Skye"

    @staticmethod
    def share_token(value: str) -> str:
        value = value.strip()
        if "agent_" in value:
            value = value.rsplit("agent_", 1)[1].split("&", 1)[0]
        if not SHARE_TOKEN.fullmatch(value):
            raise ValueError("Invalid agent share link or token.")
        return value

    @staticmethod
    def _definition(
        name: str,
        description: str,
        instructions: str,
        model: ModelId | None,
        capabilities: tuple[AgentCapability, ...],
    ) -> AgentDefinition:
        name = " ".join(name.split())
        description = " ".join(description.split())
        instructions = instructions.strip()
        if not 1 <= len(name) <= 64:
            raise ValueError("Agent name must be 1–64 characters.")
        if not 1 <= len(description) <= 240:
            raise ValueError("Description must be 1–240 characters.")
        if not 1 <= len(instructions) <= 12_000:
            raise ValueError("Instructions must be 1–12,000 characters.")
        if model is not None and not model.strip():
            raise ValueError("Unknown model.")
        capabilities = tuple(item for item in AGENT_CAPABILITIES if item in capabilities)
        payload = {
            "name": name,
            "description": description,
            "instructions": instructions,
            "model": model,
            "capabilities": capabilities,
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return AgentDefinition(
            name=name,
            description=description,
            instructions=instructions,
            model=model,
            capabilities=capabilities,
            checksum=hashlib.sha256(canonical.encode()).hexdigest(),
        )


def version_summary(version: AgentVersion) -> str:
    capabilities = ", ".join(version.capabilities) or "None"
    return f"Capabilities: {capabilities}"
