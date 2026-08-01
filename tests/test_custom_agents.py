from pathlib import Path

import pytest

from skye.custom_agents import CustomAgentService
from skye.db import Database
from skye.models import Scope


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


async def test_agents_are_scoped_and_can_be_selected(database: Database) -> None:
    service = CustomAgentService(database)
    private = Scope("user", 42)
    group = Scope("chat", -100)
    created = await service.create(
        owner_id=42,
        scope=private,
        name="Researcher",
        description="Finds and checks sources",
        instructions="Research carefully and cite the evidence.",
        capabilities=("web",),
    )

    await service.select(private, created.profile.id)

    assert (await database.get_settings(private)).active_agent_id == created.profile.id
    assert await service.list(group) == []
    composition = await service.composition(private, created.profile.id)
    assert composition.active == created
    assert composition.specialists == ()


async def test_shared_install_stays_pinned_when_owner_edits(database: Database) -> None:
    service = CustomAgentService(database)
    owner_scope = Scope("user", 42)
    imported_scope = Scope("chat", -100)
    first = await service.create(
        owner_id=42,
        scope=owner_scope,
        name="Editor",
        description="Polishes prose",
        instructions="Make prose concise.",
    )
    token = await service.share(owner_scope, first.profile.id, owner_id=42)
    imported = await service.import_shared(imported_scope, token, installed_by=7)

    second = await service.edit(
        agent_id=first.profile.id,
        owner_id=42,
        scope=owner_scope,
        name="Editor",
        description="Polishes prose",
        instructions="Make prose concise and warm.",
        model=None,
        capabilities=first.version.capabilities,
    )

    assert first.version.version == 1
    assert imported.version.version == 1
    assert second.version.version == 2
    assert (await service.require_installed(imported_scope, first.profile.id)).version.version == 1
    shared = await service.import_shared(Scope("user", 9), token, installed_by=9)
    assert shared.version.instructions == "Make prose concise."


async def test_only_owner_can_edit_or_share(database: Database) -> None:
    service = CustomAgentService(database)
    scope = Scope("user", 42)
    created = await service.create(
        owner_id=42,
        scope=scope,
        name="Planner",
        description="Builds plans",
        instructions="Create a practical plan.",
    )

    with pytest.raises(PermissionError):
        await service.share(scope, created.profile.id, owner_id=7)
    with pytest.raises(PermissionError):
        await service.edit(
            agent_id=created.profile.id,
            owner_id=7,
            scope=scope,
            name=created.version.name,
            description=created.version.description,
            instructions=created.version.instructions,
            model=None,
            capabilities=created.version.capabilities,
        )


async def test_group_created_agent_remains_in_owner_library(database: Database) -> None:
    service = CustomAgentService(database)
    group = Scope("chat", -100)
    created = await service.create(
        owner_id=42,
        scope=group,
        name="Facilitator",
        description="Keeps group discussions focused",
        instructions="Summarize decisions and unresolved questions.",
    )

    personal = await service.require_installed(Scope("user", 42), created.profile.id)
    assert personal.version == created.version

    await service.remove(group, created.profile.id)
    assert await service.require_installed(Scope("user", 42), created.profile.id) == personal


async def test_removing_active_install_returns_scope_to_skye(database: Database) -> None:
    service = CustomAgentService(database)
    scope = Scope("user", 42)
    created = await service.create(
        owner_id=42,
        scope=scope,
        name="Planner",
        description="Builds plans",
        instructions="Create a practical plan.",
    )
    await service.select(scope, created.profile.id)

    assert await service.remove(scope, created.profile.id)
    assert (await database.get_settings(scope)).active_agent_id is None
    assert await service.list(scope) == []
