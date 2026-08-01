from pathlib import Path

import pytest

from skye.db import Database
from skye.memory import MemoryService
from skye.models import Scope


@pytest.fixture
async def memory(tmp_path: Path):
    database = Database(tmp_path / "memory.db", "gpt-5.6-luna", "medium")
    await database.open()
    try:
        yield MemoryService(database)
    finally:
        await database.close()


async def test_context_includes_preferences_and_relevant_facts(memory: MemoryService) -> None:
    scope = Scope("user", 7)
    await memory.remember(scope, "Prefers concise answers", "preference")
    await memory.remember(scope, "Project Aurora uses Python", "project")

    context = await memory.context(scope, "What language does Aurora use?")

    assert "Prefers concise answers" in context
    assert "Project Aurora uses Python" in context


async def test_memory_content_is_normalized_and_bounded(memory: MemoryService) -> None:
    scope = Scope("user", 7)

    saved = await memory.remember(scope, "  Likes   tea  ", "preference")

    assert saved.content == "Likes tea"
    with pytest.raises(ValueError):
        await memory.remember(scope, "x" * 501)
